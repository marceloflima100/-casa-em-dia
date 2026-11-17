#!/usr/bin/env python3
"""Resumo do Casa em Dia no Telegram.

Só manda mensagem quando existe algo pendente de verdade. Lembrete de
calendário ("preencha o sistema") vira ruído e passa a ser ignorado em poucas
semanas; aviso que chega com a informação dentro, não.

Lê o banco autenticado como um usuário comum do Supabase (o "robô"), então
enxerga exatamente o que o RLS permite. Nenhuma chave service_role é usada.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_KEY"]
EMAIL = os.environ["BOT_EMAIL"]
SENHA = os.environ["BOT_SENHA"]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT"]
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
    """Decide se vale avisar e monta o texto. Função pura, para poder testar."""
    linhas = []

    if vencidos:
        total = sum(float(t.get("amount") or 0) for t in vencidos)
        linhas.append(
            f"⚠️ *{len(vencidos)} lançamentos previstos já venceram* "
            f"e seguem sem confirmação — {brl(total)}"
        )
        for t in vencidos[:5]:
            d = datetime.strptime(t["transaction_date"], "%Y-%m-%d").date()
            linhas.append(f"    • {d.strftime('%d/%m')} — {t['description']} — {brl(t.get('amount'))}")
        if len(vencidos) > 5:
            linhas.append(f"    • … e mais {len(vencidos) - 5}")

    if ultima_atualizacao is not None:
        dias = (hoje - ultima_atualizacao).days
        if dias >= 7:
            linhas.append(
                f"🕗 Nenhum lançamento novo há *{dias} dias* "
                f"(o último foi em {ultima_atualizacao.strftime('%d/%m')})"
            )

    # a Itaú Black fecha dia 15 e vence dia 17: do dia 16 em diante a fatura já saiu
    competencia_atual = hoje.strftime("%Y-%m")
    if hoje.day >= 16 and (ultima_fatura or "") < competencia_atual:
        rotulo = ultima_fatura or "nenhuma ainda"
        linhas.append(
            f"💳 Fatura de {hoje.strftime('%m/%Y')} ainda não importada "
            f"(última: {rotulo})"
        )

    if not linhas:
        return False, ""

    linhas.append("")
    linhas.append(f"[Abrir o Casa em Dia]({APP})")
    return True, "*Casa em Dia*\n\n" + "\n".join(linhas)


def enviar(texto):
    corpo = json.dumps(
        {
            "chat_id": CHAT,
            "text": texto,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
    ).encode()
    http(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        corpo,
        {"Content-Type": "application/json"},
        "POST",
    )


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

    deve_avisar, texto = montar(hoje, vencidos, ultima_atualizacao, ultima_fatura)
    if not deve_avisar:
        print("Nada pendente — nenhuma mensagem enviada.")
        return

    enviar(texto)
    print(f"Mensagem enviada ao Telegram ({len(texto)} caracteres).")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode()[:200]
        print(f"::warning::Resumo do Telegram falhou: HTTP {e.code} — {detalhe}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::Resumo do Telegram falhou: {e}")
        sys.exit(1)
