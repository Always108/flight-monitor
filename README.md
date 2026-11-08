# ✈️ Flight Monitor

Monitor automático de preços de passagens aéreas, desenvolvido em **Python**, com pesquisas pelo Google Flights através da SerpApi e notificações por e-mail.

O objetivo é encontrar passagens de ida e volta abaixo de um preço definido, sem precisar pesquisar manualmente todos os dias.

## 🚀 Funcionalidades

- 🔎 Pesquisa automática de passagens aéreas.
- 💰 Limites de preços configuráveis para cada destino.
- 📧 Alertas de promoções enviados pelo Gmail.
- 🔥 Identificação de promoções excelentes.
- 🛫 Informações sobre companhias aéreas, horários e escalas.
- ⭐ Destaque para voos diretos na ida e na volta.
- 💾 Registro do último preço notificado para evitar alertas repetidos.
- ⏰ Execução automática através do GitHub Actions.
- ☁️ Funciona sem precisar manter o computador ligado.

## 🛠️ Tecnologias utilizadas

- **Python:** lógica de pesquisa e envio de notificações.
- **SerpApi:** consulta de passagens no Google Flights.
- **GitHub Actions:** execução automática na nuvem.
- **YAML:** configuração das rotas e do agendamento.
- **Gmail SMTP:** envio de notificações por e-mail.

## ⚙️ Como funciona?

1. O GitHub Actions inicia o programa nos horários configurados.
2. O Python consulta os preços das passagens através da SerpApi.
3. O programa compara os valores encontrados com os limites definidos.
4. Quando encontra uma oferta que atende aos critérios, envia um e-mail.
5. O último preço notificado é registrado para evitar notificações repetidas.

**Importante:** os preços podem mudar e a disponibilidade da passagem deve ser confirmada antes da compra.

## 📁 Estrutura do projeto

```text
.github/
└── workflows/
    └── check-flights.yml

flight-monitor/
├── flight_monitor.py
├── config.yaml
├── requirements.txt
└── alert_state.json
```

## 📋 Como utilizar sua própria cópia

### 1. Faça um Fork

Clique no botão **Fork**, localizado na parte superior deste repositório, e crie uma cópia na sua conta do GitHub.

### 2. Configure suas pesquisas

Edite o arquivo:

`flight-monitor/config.yaml`

Personalize os aeroportos, as datas, os limites de preços e as demais opções disponíveis.

### 3. Configure suas credenciais

Em seu repositório, acesse:

**Settings → Secrets and variables → Actions**

Crie os seguintes *Repository secrets*:

| Secret | Descrição |
|---|---|
| `SERPAPI_KEY` | Chave da API SerpApi |
| `SMTP_USER` | Conta Gmail utilizada para enviar os alertas |
| `SMTP_PASSWORD` | Senha de aplicativo do Gmail |
| `EMAIL_TO` | Endereço de e-mail que receberá os alertas |

Nunca coloque senhas ou chaves de API diretamente no código.

### 4. Ative o GitHub Actions

Na aba **Actions**, habilite os workflows caso o GitHub solicite.

Verifique também se o workflow possui permissão para atualizar o arquivo de histórico:

```yaml
permissions:
  contents: write
```

### 5. Execute o monitor

Na aba **Actions**, selecione o workflow **Check Flight Prices** e utilize **Run workflow**.

Para testar somente o envio de e-mail, marque a opção `test_email`.

Para executar uma pesquisa real, deixe essa opção desmarcada.

O monitor também pode executar automaticamente nos horários definidos no arquivo `check-flights.yml`.

**Observação:** o GitHub Actions pode atrasar ou ocasionalmente ignorar execuções agendadas. Além disso, as consultas à SerpApi estão sujeitas aos limites do plano contratado.

## 🔐 Segurança

As credenciais são armazenadas nos GitHub Actions Secrets e não devem ser publicadas no repositório.

Cada pessoa que fizer um Fork deverá configurar suas próprias credenciais.

## 🤝 Contribuições

Sugestões, melhorias e correções são bem-vindas!

Você pode criar um Fork, modificar o código e abrir um Pull Request para sugerir alterações ao projeto original.

## 📌 Aviso

Este é um projeto pessoal e educacional, sem vínculo oficial com companhias aéreas, Google Flights ou SerpApi.

O monitor não realiza reservas nem garante a disponibilidade dos preços encontrados.

---

Desenvolvido com Python 🐍 e GitHub Actions ⚙️.
