"""
WhatsApp notification helper for Kuziini.
Uses CallMeBot free API (https://www.callmebot.com/blog/free-api-whatsapp-messages/)

Settings stored in Redis key app:settings:
  {
    "wa_phone":  "40723333221",   # recipient phone in international format (no +)
    "wa_apikey": "1234567",       # CallMeBot API key received after activation
  }

One-time activation on the recipient phone:
  1. Save +34 644 51 98 70 as a WhatsApp contact
  2. Send: I allow callmebot to send me messages
  3. You'll receive your API key - enter it in Admin → Setari
"""
import urllib.request
import urllib.parse
import os


ACTION_LABELS = {
    'offer_save':    'Oferta salvata 💾',
    'export_excel':  'Export Excel 📊',
    'export_pdf':    'Print / PDF 🖨️',
    'whatsapp_share': 'Trimis pe WhatsApp 📱',
}


def _get_settings():
    """Load WhatsApp settings from Redis via auth_utils."""
    try:
        import auth_utils
        s = auth_utils.get_app_settings()
        return s.get('wa_phone', ''), s.get('wa_apikey', '')
    except Exception:
        return '', ''


def send_message(phone: str, apikey: str, text: str) -> bool:
    """Send a WhatsApp message via CallMeBot. Returns True on success."""
    if not phone or not apikey:
        return False
    try:
        encoded = urllib.parse.quote(text)
        url = f'https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded}&apikey={apikey}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Kuziini/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


def notify(action: str, agent_name: str, agent_username: str, offer: dict | None = None) -> bool:
    """
    Send notification to the configured WhatsApp number.
    Reads phone + apikey from Redis app:settings.
    """
    phone, apikey = _get_settings()
    if not phone or not apikey:
        return False  # not configured yet

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
            lines.append(f'📦 {len(prods)} ref., {qty} buc.')
        if offer.get('date'):
            lines.append(f'📅 {offer["date"]}')

    return send_message(phone, apikey, '\n'.join(lines))


# Keep old name for backwards compat
def notify_madalin(action, agent_name, agent_username, offer=None):
    return notify(action, agent_name, agent_username, offer)
