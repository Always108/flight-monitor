#!/usr/bin/env python3
"""
Flight Monitor - Monitora passagens aéreas via AviationStack API
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
    excellent_price_brl: float = 0.0

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

    # AviationStack key vem do secret
    config["aviationstack"]["api_key"] = os.getenv("AVIATIONSTACK_KEY", config["aviationstack"].get("api_key"))

    # Evolution API
    config["evolution_api"]["base_url"] = os.getenv("EVOLUTION_BASE_URL", config["evolution_api"].get("base_url"))
    config["evolution_api"]["instance"] = os.getenv("EVOLUTION_INSTANCE", config["evolution_api"].get("instance"))
    config["evolution_api"]["token"] = os.getenv("EVOLUTION_TOKEN", config["evolution_api"].get("token"))

    # Email
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
    last_price = state.last_notified_price.get(route_key)

    # Primeira oferta encontrada dentro do limite
    if last_price is None:
        log.info(f"[{route_key}] Primeira promoção: R$ {price:.2f}")
        return True

    # Nova oferta mais barata que a última notificada
    if price < last_price - 0.01:
        log.info(
            f"[{route_key}] Novo preço menor: "
            f"R$ {last_price:.2f} → R$ {price:.2f}"
        )
        return True

    # Mesmo preço ou preço maior: não repetir o alerta
    log.info(
        f"[{route_key}] Sem nova queda de preço "
        f"(atual: R$ {price:.2f}, último alerta: R$ {last_price:.2f})"
    )
    return False


# ──────────────────────────────────────────────
# AviationStack API
# ──────────────────────────────────────────────


def search_flights(route: Route, config: Dict) -> List[FlightOffer]:
    """Pesquisa passagens de ida e volta pelo Google Flights via SerpApi."""
    import os

    travel = config["travel"]
    api_key = os.environ.get("SERPAPI_KEY")

    if not api_key:
        log.error("SERPAPI_KEY não configurada")
        return []

    params = {
        "engine": "google_flights",
        "api_key": api_key,
        "departure_id": route.origin,
        "arrival_id": route.destination,
        "outbound_date": travel["departure_date"],        
        "return_date": travel["return_date"],
        "type": "1",  # Ida e volta
        "currency": "BRL",
        "hl": "pt",
        "adults": travel.get("adults", 1),
    }

    try:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=30,
        )
        
        data = resp.json()

        if resp.status_code != 200:
            log.error(
                f"SerpApi retornou HTTP {resp.status_code}: "
                f"{data.get('error', 'Erro sem descrição')}"
            )
            return []

        if data.get("error"):
            log.error(f"Erro SerpApi [{route.label}]: {data['error']}")
            return []

    except (requests.RequestException, ValueError) as e:
        log.error(f"Erro SerpApi [{route.label}]: {e}")
        return []

    offers = []

    results = data.get("best_flights", []) + data.get("other_flights", [])
    
    log.info(
        f"[{route.label}] SerpApi: "
        f"{len(results)} opções encontradas; "
        f"tipo de viagem: {params['type']}; "
        f"data de volta: {params['return_date']}"
    )    
        
    if results:
        first_result = results[0]
        log.info(
            f"[{route.label}] Diagnóstico da primeira oferta: "
            f"preço={first_result.get('price')}; "
            f"departure_token_presente={bool(first_result.get('departure_token'))}; "
            f"chaves={list(first_result.keys())}"
        )
    for item in results:
        try:
            flights = item.get("flights", [])
            price = item.get("price")

            if not flights or price is None:
                continue

            first = flights[0]
            last = flights[-1]

            dep = first.get("departure_airport", {})
            arr = last.get("arrival_airport", {})

            offers.append(FlightOffer(
                route=route,
                price_brl=float(price),
                currency="BRL",
                departure_date=travel["departure_date"],
                return_date=travel["return_date"],
                airline=first.get("airline", "Não informado"),
                departure_time=dep.get("time", "")[-5:],
                arrival_time=arr.get("time", "")[-5:],
                duration=f"{item.get('total_duration', 0)} min",
                stops=max(0, len(flights) - 1),                
                booking_link=(
                    "https://www.google.com/travel/flights"
                    f"?q=Flights%20from%20{route.origin}"
                    f"%20to%20{route.destination}"
                    f"%20on%20{travel['departure_date']}"
                    f"%20returning%20{travel['return_date']}"
                ),

                raw=item,
            ))

        except (KeyError, TypeError, ValueError) as e:
            log.warning(f"Erro processando oferta: {e}")

    return sorted(offers, key=lambda x: x.price_brl)

# ──────────────────────────────────────────────
# Notificações (inalterado)
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


def format_alert_message(offers: List[FlightOffer], config: Dict):
    
    travel = config["travel"]
    route = offers[0].route

    best_price = offers[0].price_brl

    excellent_limit = next(
        (
            r.get("excellent_price_brl", r["max_price_brl"])
            for r in config["routes"]
            if r["origin"] == route.origin
            and r["destination"] == route.destination
        ),
        route.max_price_brl,
    )

    if best_price <= excellent_limit:
        category = "🔥 PROMOÇÃO EXCELENTE"
    else:
        category = "✈️ PREÇO INTERESSANTE"

    subject = (
        f"{category}: {route.label} por R$ {best_price:.2f} "
        f"(ida {travel['departure_date']} / volta {travel['return_date']})"
    )


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
                    <th style="padding: 10px; text-align: left;">Horário da ida</th>
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
    log.info("🚀 Iniciando Flight Monitor (AviationStack)")

    config = load_config()
    state = load_state()

    # Validações    
    
    required = [
        ("SERPAPI_KEY", os.environ.get("SERPAPI_KEY")),
        ("email.smtp_user", config["email"].get("smtp_user")),
        ("email.smtp_password", config["email"].get("smtp_password")),
        ("email.to", config["email"].get("to")),
    ]

    missing = [k for k, v in required if not v]
    if missing:
        log.error(f"Configurações obrigatórias ausentes: {missing}")
        return 1


    routes = [Route(**r) for r in config["routes"]]
    any_alert = False

    for route in routes:
        log.info(f"🔍 Buscando: {route.label} (threshold: R$ {route.max_price_brl:.2f})")
        offers = search_flights(route, config)

        if not offers:
            log.warning(f"[{route.label}] Nenhuma oferta com preço retornada (free plan pode não incluir preço)")
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
            whatsapp_ok = False  # WhatsApp temporariamente desativado

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
