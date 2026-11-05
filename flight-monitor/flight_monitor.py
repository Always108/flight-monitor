#!/usr/bin/env python3
"""
Flight Monitor - Monitora passagens aéreas via Amadeus API
e notifica via Email (Gmail SMTP) + WhatsApp (Evolution API).
"""

import os
import sys
import json
import yaml
import smtplib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

import requests
from amadeus import Client, ResponseError
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# Configuração de logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────
@dataclass
class Route:
    origin: str
    destination: str
    max_price_brl: float
    label: str

@dataclass
class FlightOffer:
    route: Route
    price_brl: float
    currency: str
    departure_date: str
    return_date: str
    airline: str
    departure_time: str
    arrival_time: str
    duration: str
    stops: int
    booking_link: str
    raw: Dict[str, Any]

@dataclass
class AlertState:
    last_notified_price: Dict[str, float]
    last_notified_at: Dict[str, str]

# ──────────────────────────────────────────────
# Utilitários
# ──────────────────────────────────────────────
def load_config() -> Dict:
    load_dotenv()

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["amadeus"]["client_id"] = os.getenv("AMADEUS_CLIENT_ID", config["amadeus"].get("client_id"))
    config["amadeus"]["client_secret"] = os.getenv("AMADEUS_CLIENT_SECRET", config["amadeus"].get("client_secret"))
    config["evolution_api"]["base_url"] = os.getenv("EVOLUTION_BASE_URL", config["evolution_api"].get("base_url"))
    config["evolution_api"]["instance"] = os.getenv("EVOLUTION_INSTANCE", config["evolution_api"].get("instance"))
    config["evolution_api"]["token"] = os.getenv("EVOLUTION_TOKEN", config["evolution_api"].get("token"))
    config["email"]["smtp_user"] = os.getenv("SMTP_USER", config["email"].get("smtp_user"))
    config["email"]["smtp_password"] = os.getenv("SMTP_PASSWORD", config["email"].get("smtp_password"))
    config["email"]["to"] = os.getenv("EMAIL_TO", config["email"].get("to"))

    return config


def load_state() -> AlertState:
    state_file = Path("alert_state.json")
    if state_file.exists():
        with open(state_file, "r") as f:
            data = json.load(f)
        return AlertState(**data)
    return AlertState(last_notified_price={}, last_notified_at={})


def save_state(state: AlertState) -> None:
    with open("alert_state.json", "w") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)


def should_alert(route_key: str, price: float, state: AlertState, config: Dict) -> bool:
    cooldown_h = config["alert"]["cooldown_hours"]
    drop_pct = config["alert"]["price_drop_threshold_pct"]

    last_price = state.last_notified_price.get(route_key)
    last_at_str = state.last_notified_at.get(route_key)

    if last_at_str:
        last_at = datetime.fromisoformat(last_at_str)
        if datetime.now() - last_at < timedelta(hours=cooldown_h):
            if last_price and price <= last_price * (1 - drop_pct / 100):
                log.info(f"[{route_key}] Queda de {drop_pct}%+ detectada (R$ {last_price:.2f} → R$ {price:.2f}) — alertando!")
                return True
            log.debug(f"[{route_key}] Em cooldown ({cooldown_h}h), preço R$ {price:.2f} — silencioso")
            return False

    return True


# ──────────────────────────────────────────────
# Amadeus API
# ──────────────────────────────────────────────
def create_amadeus_client(config: Dict) -> Client:
    return Client(
        client_id=config["amadeus"]["client_id"],
        client_secret=config["amadeus"]["client_secret"],
        hostname=config["amadeus"]["base_url"].replace("https://", ""),
        log_level="warning",
    )


def search_flights(amadeus: Client, route: Route, config: Dict) -> List[FlightOffer]:
    travel = config["travel"]

    try:
        response = amadeus.shopping.flight_offers_search.get(
            originLocationCode=route.origin,
            destinationLocationCode=route.destination,
            departureDate=travel["departure_date"],
            returnDate=travel["return_date"],
            adults=travel["adults"],
            currencyCode=travel["currency"],
            max=travel["max_results_per_route"],
        )
    except ResponseError as e:
        log.error(f"Erro Amadeus [{route.label}]: {e}")
        return []

    offers = []
    for offer in response.data:
        try:
            price_brl = float(offer["price"]["total"])
            currency = offer["price"]["currency"]

            itinerary = offer["itineraries"][0]
            segments = itinerary["segments"]
            first_seg = segments[0]
            last_seg = segments[-1]

            departure_time = first_seg["departure"]["at"][11:16]
            arrival_time = last_seg["arrival"]["at"][11:16]

            duration = itinerary["duration"].replace("PT", "").replace("H", "h").replace("M", "m")

            stops = len(segments) - 1
            airline = segments[0]["carrierCode"]

            booking_link = f"https://www.google.com/travel/flights?q=Flights%20from%20{route.origin}%20to%20{route.destination}%20on%20{travel['departure_date']}%20return%20{travel['return_date']}"

            offers.append(FlightOffer(
                route=route,
                price_brl=price_brl,
                currency=currency,
                departure_date=travel["departure_date"],
                return_date=travel["return_date"],
                airline=airline,
                departure_time=departure_time,
                arrival_time=arrival_time,
                duration=duration,
                stops=stops,
                booking_link=booking_link,
                raw=offer,
            ))
        except (KeyError, ValueError, IndexError) as e:
            log.warning(f"Erro parseando oferta: {e}")
            continue

    return sorted(offers, key=lambda x: x.price_brl)


# ──────────────────────────────────────────────
# Notificações
# ──────────────────────────────────────────────
def send_email(config: Dict, subject: str, html_body: str) -> bool:
    email_cfg = config["email"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f'{email_cfg["from_name"]} <{email_cfg["smtp_user"]}>'
    msg["To"] = email_cfg["to"]
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(email_cfg["smtp_user"], email_cfg["smtp_password"])
            server.send_message(msg)
        log.info(f"Email enviado para {email_cfg['to']}")
        return True
    except Exception as e:
        log.error(f"Falha ao enviar email: {e}")
        return False


def send_whatsapp(config: Dict, message: str) -> bool:
    evo = config["evolution_api"]

    url = f"{evo['base_url']}/message/sendText/{evo['instance']}"
    headers = {
        "apikey": evo["token"],
        "Content-Type": "application/json",
    }
    payload = {
        "number": evo.get("target_number", "55XXXXXXXXXXX"),
        "text": message,
        "delay": evo.get("send_delay_seconds", 2),
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        log.info("WhatsApp enviado via Evolution API")
        return True
    except Exception as e:
        log.error(f"Falha ao enviar WhatsApp: {e}")
        return False


def format_alert_message(offers: List[FlightOffer], config: Dict) -> tuple[str, str, str]:
    travel = config["travel"]
    route = offers[0].route

    subject = f"✈️ Promoção: {route.label} por R$ {offers[0].price_brl:.2f} (ida {travel['departure_date']} / volta {travel['return_date']})"

    rows = ""
    for o in offers:
        stops_text = "Direto" if o.stops == 0 else f"{o.stops} parada(s)"
        rows += f"""
        <tr>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{o.airline}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">R$ {o.price_brl:.2f}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{o.departure_time} – {o.arrival_time}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{o.duration}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">{stops_text}</td>
            <td style="padding: 8px; border-bottom: 1px solid #eee;">
                <a href="{o.booking_link}" style="color: #1a73e8;">Ver no Google Flights</a>
            </td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #1a73e8;">✈️ Alerta de Passagem Aérea</h2>
        <p><strong>Rota:</strong> {route.label}</p>
        <p><strong>Datas:</strong> Ida {travel['departure_date']} | Volta {travel['return_date']}</p>
        <p><strong>Threshold:</strong> ≤ R$ {route.max_price_brl:.2f}</p>
        <hr>
        <table style="width: 100%; border-collapse: collapse; margin-top: 16px;">
            <thead>
                <tr style="background: #f5f5f5;">
                    <th style="padding: 10px; text-align: left;">Cia</th>
                    <th style="padding: 10px; text-align: left;">Preço</th>
                    <th style="padding: 10px; text-align: left;">Horário</th>
                    <th style="padding: 10px; text-align: left;">Duração</th>
                    <th style="padding: 10px; text-align: left;">Paradas</th>
                    <th style="padding: 10px; text-align: left;">Ação</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        <p style="margin-top: 20px; color: #666; font-size: 13px;">
            Enviado automaticamente pelo Flight Monitor · {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </p>
    </body>
    </html>
    """

    whatsapp_lines = [
        f"✈️ *PROMOÇÃO: {route.label}*",
        f"📅 Ida: {travel['departure_date']} | Volta: {travel['return_date']}",
        f"💰 Threshold: ≤ R$ {route.max_price_brl:.2f}",
        "",
    ]
    for o in offers[:3]:
        stops_text = "Direto" if o.stops == 0 else f"{o.stops} parada(s)"
        whatsapp_lines.append(f"• {o.airline} | R$ {o.price_brl:.2f} | {o.departure_time}-{o.arrival_time} | {o.duration} | {stops_text}")
    whatsapp_lines.append(f"\n🔗 {offers[0].booking_link}")
    whatsapp_lines.append(f"\n_Flight Monitor · {datetime.now().strftime('%d/%m %H:%M')}_")

    return subject, html, "\n".join(whatsapp_lines)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main() -> int:
    log.info("🚀 Iniciando Flight Monitor")

    config = load_config()
    state = load_state()

    required = [
        ("amadeus.client_id", config["amadeus"].get("client_id")),
        ("amadeus.client_secret", config["amadeus"].get("client_secret")),
        ("evolution_api.base_url", config["evolution_api"].get("base_url")),
        ("evolution_api.instance", config["evolution_api"].get("instance")),
        ("evolution_api.token", config["evolution_api"].get("token")),
        ("email.smtp_user", config["email"].get("smtp_user")),
        ("email.smtp_password", config["email"].get("smtp_password")),
        ("email.to", config["email"].get("to")),
    ]
    missing = [k for k, v in required if not v]
    if missing:
        log.error(f"Configurações obrigatórias ausentes: {missing}")
        return 1

    amadeus = create_amadeus_client(config)

    routes = [Route(**r) for r in config["routes"]]
    any_alert = False

    for route in routes:
        log.info(f"🔍 Buscando: {route.label} (threshold: R$ {route.max_price_brl:.2f})")
        offers = search_flights(amadeus, route, config)

        if not offers:
            log.warning(f"[{route.label}] Nenhuma oferta encontrada")
            continue

        cheap_offers = [o for o in offers if o.price_brl <= route.max_price_brl]

        if not cheap_offers:
            best = offers[0]
            log.info(f"[{route.label}] Melhor preço: R$ {best.price_brl:.2f} (acima de R$ {route.max_price_brl:.2f})")
            continue

        route_key = f"{route.origin}-{route.destination}"

        if should_alert(route_key, cheap_offers[0].price_brl, state, config):
            subject, html, whatsapp_msg = format_alert_message(cheap_offers, config)

            email_ok = send_email(config, subject, html)
            whatsapp_ok = send_whatsapp(config, whatsapp_msg)

            if email_ok or whatsapp_ok:
                state.last_notified_price[route_key] = cheap_offers[0].price_brl
                state.last_notified_at[route_key] = datetime.now().isoformat()
                save_state(state)
                any_alert = True
                log.info(f"✅ Alerta enviado para {route.label} — R$ {cheap_offers[0].price_brl:.2f}")
            else:
                log.error(f"❌ Falha ao enviar alertas para {route.label}")
        else:
            log.info(f"[{route.label}] Promoção ativa mas em cooldown — silencioso")

    if not any_alert:
        log.info("ℹ️ Nenhuma promoção nova encontrada")

    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

5️⃣ Cria o workflow do GitHub Actions

mkdir .github\workflows
cat > .github\workflows\check-flights.yml << 'EOF'
name: Check Flight Prices

on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  check-flights:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run Flight Monitor
        env:
          AMADEUS_CLIENT_ID: ${{ secrets.AMADEUS_CLIENT_ID }}
          AMADEUS_CLIENT_SECRET: ${{ secrets.AMADEUS_CLIENT_SECRET }}
          EVOLUTION_BASE_URL: ${{ secrets.EVOLUTION_BASE_URL }}
          EVOLUTION_INSTANCE: ${{ secrets.EVOLUTION_INSTANCE }}
          EVOLUTION_TOKEN: ${{ secrets.EVOLUTION_TOKEN }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
        run: |
          python flight_monitor.py

      - name: Commit alert_state.json if changed
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          if ! git diff --quiet alert_state.json; then
            git add alert_state.json
            git commit -m "chore: update alert state [skip ci]"
            git push
          else
            echo "No state changes"
          fi
EOF
