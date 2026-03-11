"""
WhatsApp notification helper for Kuziini.
Uses CallMeBot free API to send messages to Madalin (+40723333221).

One-time setup required on +40723333221:
  1. Save +34 644 51 98 70 as a WhatsApp contact
  2. Send: I allow callmebot to send me messages
  3. You'll receive an apikey — set it as CALLMEBOT_API_KEY env variable on Vercel

CallMeBot API: https://api.callmebot.com/whatsapp.php?phone=PHONE&text=TEXT&apikey=KEY
"""
import os
import urllib.request
import urllib.parse


MADALIN_PHONE = '40723333221'   # +40723333221 in international format without +

ACTION_LABELS = {
    'offer_save':    'Oferta salvata',
    'export_excel':  'Export Excel',
    'export_pdf':    'Print / PDF',
    'offer_create':  'Oferta creata',
}


def _send(phone: str, text: str) -> bool:
    """Send a WhatsApp message via CallMeBot. Returns True on success."""
    api_key = os.environ.get('CALLMEBOT_API_KEY', '').strip()
    if not api_key:
        return False  # API key not configured yet
    try:
        encoded = urllib.parse.quote(text)
        url = f'https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded}&apikey={api_key}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Kuziini/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


def notify_madalin(action: str, agent_name: str, agent_username: str, offer: dict | None = None) -> bool:
    """
    Send notification to Madalin when an offer action occurs.

    action: 'offer_save' | 'export_excel' | 'export_pdf'
    offer: dict with keys num, client, total, products (list)
    """
    label = ACTION_LABELS.get(action, action)
    lines = [
        f'*Kuziini* — {label}',
        f'👤 Agent: {agent_name} ({agent_username})',
    ]
    if offer:
        if offer.get('num'):
            lines.append(f'📋 Oferta: {offer["num"]}')
        if offer.get('client'):
            lines.append(f'🏢 Client: {offer["client"]}')
        if offer.get('total'):
            try:
                lines.append(f'💰 Total: {float(offer["total"]):.2f} Lei')
            except (TypeError, ValueError):
                pass
        prods = offer.get('products') or []
        if prods:
            qty = sum(p.get('qty', 1) for p in prods)
            lines.append(f'📦 Produse: {len(prods)} referinte, {qty} buc.')
        if offer.get('date'):
            lines.append(f'📅 Data: {offer["date"]}')
    text = '\n'.join(lines)
    return _send(MADALIN_PHONE, text)
