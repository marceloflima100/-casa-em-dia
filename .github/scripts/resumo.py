#!/usr/bin/env python3
"""Resumo do Casa em Dia: avisa por Telegram, e-mail, ou os dois.

Só manda mensagem quando existe algo pendente de verdade. Lembrete de
calendário ("preencha o sistema") vira ruído e passa a ser ignorado em poucas
semanas; aviso que chega com a informação dentro, não.

Lê o banco autenticado como um usuário comum do Supabase (o "robô"), então
enxerga exatamente o que o RLS permite. Nenhuma chave service_role é usada.

O canal vem da variável CANAIS ("telegram", "email" ou "telegram,email").
A decisão do que avisar é a mesma nos dois; muda só a entrega.
"""

import json
import os
import sys
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_KEY"]
EMAIL = os.environ["BOT_EMAIL"]
SENHA = os.environ["BOT_SENHA"]
CANAIS = [c.strip().lower() for c in os.environ.get("CANAIS", "telegram").split(",") if c.strip()]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT", "")

REMETENTE = os.environ.get("GMAIL_USER", "")
SENHA_APP = os.environ.get("GMAIL_APP_PASSWORD", "")
DESTINATARIOS = [e.strip() for e in os.environ.get("EMAIL_DESTINATARIOS", "").split(",") if e.strip()]
APP = os.environ.get("APP_URL", "https://marceloflima100.github.io/-casa-em-dia/")

FUSO = timezone(timedelta(hours=-3))  # horário de Brasília


def http(url, data=None, headers=None, metodo=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=metodo)
    with urllib.request.urlopen(req, timeout=30) as r:
        bruto = r.read().decode()
    return json.loads(bruto) if bruto else None


def entrar():
    """Autentica como o usuário-robô e devolve o token de acesso."""
    corpo = json.dumps({"email": EMAIL, "password": SENHA}).encode()
    r = http(
        f"{URL}/auth/v1/token?grant_type=password",
        corpo,
        {"apikey": KEY, "Content-Type": "application/json"},
        "POST",
    )
    return r["access_token"]


def consultar(token, caminho):
    return http(
        f"{URL}/rest/v1/{caminho}",
        headers={"apikey": KEY, "Authorization": f"Bearer {token}"},
    )


def brl(v):
    return "R$ " + f"{float(v or 0):,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


def montar(hoje, vencidos, ultima_atualizacao, ultima_fatura):
    """Decide se vale avisar e monta assunto e corpo. Função pura, para testar."""
    blocos = []      # cada bloco é um assunto fechado, com suas próprias linhas
    manchetes = []   # viram o assunto do aviso, para triar sem abrir

    if vencidos:
        total = sum(float(t.get("amount") or 0) for t in vencidos)
        linhas = [
            f"{len(vencidos)} lançamentos previstos já venceram e seguem "
            f"sem confirmação - {brl(total)}"
        ]
        for t in vencidos[:5]:
            d = datetime.strptime(t["transaction_date"], "%Y-%m-%d").date()
            linhas.append(f"  - {d.strftime('%d/%m')}  {t['description']}  {brl(t.get('amount'))}")
        if len(vencidos) > 5:
            linhas.append(f"  - ... e mais {len(vencidos) - 5}")
        blocos.append("\n".join(linhas))
        manchetes.append(f"{len(vencidos)} vencidos")

    if ultima_atualizacao is not None:
        dias = (hoje - ultima_atualizacao).days
        if dias >= 7:
            blocos.append(
                f"Nenhum lançamento novo há {dias} dias "
                f"(o último foi em {ultima_atualizacao.strftime('%d/%m')})"
            )
            manchetes.append(f"{dias} dias parado")

    # a Itaú Black fecha dia 15 e vence dia 17: do dia 16 em diante a fatura já saiu
    competencia_atual = hoje.strftime("%Y-%m")
    if hoje.day >= 16 and (ultima_fatura or "") < competencia_atual:
        rotulo = ultima_fatura or "nenhuma ainda"
        blocos.append(
            f"Fatura de {hoje.strftime('%m/%Y')} ainda não importada (última: {rotulo})"
        )
        manchetes.append("fatura pendente")

    if not blocos:
        return False, "", ""

    assunto = "Casa em Dia - " + ", ".join(manchetes)
    corpo = "\n\n".join(blocos) + "\n\nAbrir o Casa em Dia:\n" + APP + "\n"
    return True, assunto, corpo


def enviar_telegram(assunto, corpo):
    """Texto puro de propósito: sem parse_mode, um '*' ou '_' vindo do nome de
    um estabelecimento não quebra a mensagem. O Telegram já vira link sozinho."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        raise RuntimeError("TELEGRAM_TOKEN ou TELEGRAM_CHAT não configurado")
    dados = json.dumps(
        {"chat_id": TELEGRAM_CHAT, "text": assunto + "\n\n" + corpo,
         "disable_web_page_preview": True}
    ).encode()
    http(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        dados,
        {"Content-Type": "application/json"},
        "POST",
    )
    return f"Telegram: enviado ao chat {TELEGRAM_CHAT}"


def enviar_email(assunto, corpo):
    if not REMETENTE or not SENHA_APP or not DESTINATARIOS:
        raise RuntimeError("GMAIL_USER, GMAIL_APP_PASSWORD ou EMAIL_DESTINATARIOS não configurado")
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = f"Casa em Dia <{REMETENTE}>"
    msg["To"] = ", ".join(DESTINATARIOS)
    msg.set_content(corpo)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(REMETENTE, SENHA_APP)
        s.send_message(msg)
    return f"E-mail: enviado a {len(DESTINATARIOS)} destinatário(s)"


ENTREGADORES = {"telegram": enviar_telegram, "email": enviar_email}


def entregar(assunto, corpo, canais):
    """Tenta cada canal. Um canal quebrado não impede o outro de entregar."""
    sucessos, erros = [], []
    for canal in canais:
        funcao = ENTREGADORES.get(canal)
        if funcao is None:
            erros.append(f"{canal}: canal desconhecido")
            continue
        try:
            sucessos.append(funcao(assunto, corpo))
        except Exception as e:  # noqa: BLE001
            erros.append(f"{canal}: {e}")
    return sucessos, erros


def main():
    hoje = datetime.now(FUSO).date()
    token = entrar()

    vencidos = consultar(
        token,
        "transactions?select=transaction_date,description,amount"
        f"&status=eq.previsto&transaction_date=lt.{hoje.isoformat()}"
        "&order=transaction_date.asc&limit=100",
    ) or []

    recentes = consultar(
        token, "transactions?select=updated_at&order=updated_at.desc&limit=1"
    ) or []
    ultima_atualizacao = None
    if recentes and recentes[0].get("updated_at"):
        bruto = recentes[0]["updated_at"].replace("Z", "+00:00")
        ultima_atualizacao = datetime.fromisoformat(bruto).astimezone(FUSO).date()

    faturas = consultar(
        token, "card_statements?select=competence&order=competence.desc&limit=1"
    ) or []
    ultima_fatura = None
    if faturas and faturas[0].get("competence"):
        ultima_fatura = str(faturas[0]["competence"])[:7]

    deve_avisar, assunto, corpo = montar(hoje, vencidos, ultima_atualizacao, ultima_fatura)
    if not deve_avisar:
        print("Nada pendente - nenhum aviso enviado.")
        return

    sucessos, erros = entregar(assunto, corpo, CANAIS)
    for linha in sucessos:
        print(linha)
    for linha in erros:
        print(f"::warning::{linha}")
    if not sucessos:
        raise RuntimeError("nenhum canal conseguiu entregar o aviso")
    print(f"Assunto: {assunto}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode()[:200]
        print(f"::warning::Resumo falhou: HTTP {e.code} — {detalhe}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::Resumo falhou: {e}")
        sys.exit(1)
