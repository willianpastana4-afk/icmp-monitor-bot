import time
import requests
import sys
import logging
import os
from ping3 import ping
from concurrent.futures import ThreadPoolExecutor, as_completed

# =====================================================================
# 🧠 CONFIGURAÇÕES VIA VARIÁVEIS DE AMBIENTE
# =====================================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LATENCY_THRESHOLD_MS = 150        # Limite de latência em ms
CHECK_INTERVAL_SECONDS = 60       # Frequência de checagem (1 min)
REPEAT_ALERT_DELAY_SECONDS = 900  # Repetição de alerta (15 min)
MAX_THREADS = 10                  # Número máximo de clientes verificados em paralelo

# Lista de clientes monitorados
CLIENTES = [
    {"nome": "prgnet.ltda.47663", "ip": "177.104.249.105"},
    {"nome": "prgnetradarconc01", "ip": "177.104.247.155"},
    {"nome": "prgnetradarconc02", "ip": "177.104.247.152"},
    {"nome": "engeko.278", "ip": "177.104.244.75"},
    {"nome": "BBF MOJU", "ip": "177.104.253.78"},
    {"nome": "Sicoob", "ip": "177.104.253.66"},
    {"nome": "EQUATORIAL - 21197 CND", "ip": "177.104.247.149"},
    {"nome": "EQUATORIAL - 21200 MOJU", "ip": "177.104.253.45"},
    {"nome": "EXPEREO TELECOM", "ip": "177.104.253.34"},
    {"nome": "FONTAIM", "ip": "177.104.246.219"},
    {"nome": "INFOCLICK", "ip": "177.104.247.114"},
    {"nome": "MAJONAV", "ip": "177.104.246.210"},
    {"nome": "NELORE TA", "ip": "177.104.252.129"},
    {"nome": "OLEOPLAN BALANÇA", "ip": "10.0.57.254"},
    {"nome": "OLEOPLAN DEDICADO", "ip": "10.9.200.100"},
    {"nome": "OM POWER S.A", "ip": "177.104.253.62"},
    {"nome": "SAMBAZON DO BRASIL", "ip": "177.104.244.65"},
    {"nome": "SITELBRA - 60411", "ip": "177.104.247.121"},
    {"nome": "TOYO SETAL - CDP", "ip": "177.104.253.14"},
    {"nome": "Willian_Casa", "ip": "177.104.251.155"},
]

# =====================================================================
# ⚙️ CONFIGURAÇÃO DE LOG
# =====================================================================

logging.basicConfig(
    filename="icmp_monitor.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# =====================================================================
# 🌐 VARIÁVEIS DE ESTADO
# =====================================================================

CLIENT_STATUS = {}
session = requests.Session()  # Reutiliza conexão HTTP

# =====================================================================
# 📤 FUNÇÃO DE ENVIO PARA TELEGRAM
# =====================================================================

def send_telegram_message(message: str):
    """Envia uma mensagem formatada para o Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Token ou Chat ID não configurados! Defina as variáveis de ambiente.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    for attempt in range(3):
        try:
            r = session.post(url, json=payload, timeout=10)
            r.raise_for_status()
            return
        except requests.RequestException as e:
            logging.warning(f"Tentativa {attempt+1}/3 falhou ao enviar mensagem: {e}")
            time.sleep(2)

# =====================================================================
# 🛰️ FUNÇÃO DE CHECAGEM DE STATUS
# =====================================================================

def check_client_status(client: dict):
    """Realiza teste ICMP e envia alertas conforme variação de status."""
    ip = client["ip"]
    nome = client["nome"]
    delay_s = ping(ip, timeout=2, unit="s")

    current_state = CLIENT_STATUS.get(ip, {"status": "UNKNOWN", "last_alert_time": 0, "down_since": None})
    current_status = current_state["status"]
    new_status = "OK"
    message = None
    now = time.time()

    # --- HOST DOWN ---
    if delay_s is False or delay_s is None or delay_s == 0:
        new_status = "DOWN"
        time_since_last_alert = now - current_state["last_alert_time"]
        should_repeat_alert = (
            current_status == "DOWN"
            and REPEAT_ALERT_DELAY_SECONDS > 0
            and time_since_last_alert >= REPEAT_ALERT_DELAY_SECONDS
        )

        if current_status != "DOWN" or should_repeat_alert:
            if current_state["down_since"] is None:
                current_state["down_since"] = now

            downtime_minutes = (now - current_state["down_since"]) / 60
            message = (
                f"🚨 *ALERTA DE INDISPONIBILIDADE* 🚨\n"
                f"Cliente: `{nome}` ({ip})\n"
                f"Status: *DOWN* (Timeout ICMP)\n"
                f"Tempo indisponível: *{downtime_minutes:.1f} minutos*"
            )
            current_state["last_alert_time"] = now

    # --- HOST UP / LATÊNCIA ---
    else:
        delay_ms = delay_s * 1000
        if delay_ms > LATENCY_THRESHOLD_MS:
            new_status = "HIGH_LATENCY"
            if current_status != "HIGH_LATENCY":
                message = (
                    f"⚠️ *ALTA LATÊNCIA* ⚠️\n"
                    f"Cliente: `{nome}` ({ip})\n"
                    f"Latência atual: *{delay_ms:.2f} ms*\n"
                    f"Limite configurado: {LATENCY_THRESHOLD_MS} ms"
                )
        else:
            new_status = "OK"
            if current_status in ["DOWN", "HIGH_LATENCY"]:
                downtime_minutes = (
                    (now - current_state["down_since"]) / 60 if current_state.get("down_since") else 0
                )
                message = (
                    f"✅ *RECUPERADO* ✅\n"
                    f"Cliente: `{nome}` ({ip})\n"
                    f"Status anterior: *{current_status}*\n"
                    f"Latência atual: {delay_ms:.2f} ms\n"
                    f"Tempo indisponível: *{downtime_minutes:.1f} minutos*"
                )
                current_state["down_since"] = None

    # --- SALVAR STATUS E ENVIAR ALERTA ---
    current_state["status"] = new_status
    CLIENT_STATUS[ip] = current_state

    if message:
        logging.info(f"Enviando alerta: {message.splitlines()[0]}")
        send_telegram_message(message)

    # Logs locais
    if new_status == "OK":
        logging.info(f"[{nome} ({ip})] OK - {delay_ms:.2f} ms")
    elif new_status == "HIGH_LATENCY":
        logging.warning(f"[{nome} ({ip})] Alta latência - {delay_ms:.2f} ms")
    elif new_status == "DOWN":
        logging.error(f"[{nome} ({ip})] DOWN - Timeout ICMP")

# =====================================================================
# 🔁 LOOP PRINCIPAL DE MONITORAMENTO
# =====================================================================

def main_loop():
    global REPEAT_ALERT_DELAY_SECONDS

    if REPEAT_ALERT_DELAY_SECONDS < CHECK_INTERVAL_SECONDS and REPEAT_ALERT_DELAY_SECONDS != 0:
        REPEAT_ALERT_DELAY_SECONDS = CHECK_INTERVAL_SECONDS
        logging.warning("Ajustado REPEAT_ALERT_DELAY_SECONDS para não ser menor que CHECK_INTERVAL_SECONDS")

    logging.info("🚀 ICMP Monitor Bot Iniciado")
    logging.info(f"Monitorando {len(CLIENTES)} clientes a cada {CHECK_INTERVAL_SECONDS}s")
    logging.info(f"Repetição de alertas DOWN: {REPEAT_ALERT_DELAY_SECONDS // 60} minutos")

    while True:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n--- Checagem iniciada em {timestamp} ---")
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = [executor.submit(check_client_status, c) for c in CLIENTES]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logging.error(f"Erro ao checar cliente: {e}", exc_info=True)
        print(f"--- Checagem concluída. Próxima em {CHECK_INTERVAL_SECONDS}s ---")
        time.sleep(CHECK_INTERVAL_SECONDS)

# =====================================================================
# ▶️ EXECUÇÃO
# =====================================================================

if __name__ == "__main__":
    main_loop()
