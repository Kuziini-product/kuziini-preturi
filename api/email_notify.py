"""
Email notification helper for Kuziini.
Uses Resend free API (100 emails/day free tier).

Setup:
  1. Go to https://resend.com and create a free account
  2. Get your API key from the dashboard
  3. Add RESEND_API_KEY to Vercel environment variables
  4. (Optional) Add a custom domain in Resend for branded emails
"""
import json
import os
import urllib.request
import urllib.parse


RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
FROM_EMAIL = 'Kuziini <onboarding@resend.dev>'  # free tier default sender


def _get_user_emails():
    """Load email notification settings from Redis. Returns dict {username: email}."""
    try:
        import auth_utils
        s = auth_utils.get_app_settings()
        return s.get('notify_emails', {})
    except Exception:
        return {}


def send_email(to_email, subject, html_body):
    """Send an email via Resend API. Returns True on success."""
    api_key = RESEND_API_KEY
    if not api_key or not to_email:
        return False
    try:
        payload = json.dumps({
            'from': FROM_EMAIL,
            'to': [to_email],
            'subject': subject,
            'html': html_body,
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.resend.com/emails',
            data=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f'[Email] Error sending to {to_email}: {e}')
        return False


def notify_chat_message(sender_name, sender_username, text, recipients=None, offer_ref=None):
    """
    Send email notification to recipients of a chat message.
    Looks up each recipient's email from app settings.
    """
    user_emails = _get_user_emails()
    if not user_emails:
        return False

    subject = f'Kuziini - Mesaj nou de la {sender_name}'
    if offer_ref:
        subject += f' (Oferta #{offer_ref})'

    preview = text[:300] + ('...' if len(text) > 300 else '')

    html = f'''
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px">
      <div style="background:linear-gradient(135deg,#1e1b4b,#3b0764);color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">
        <h2 style="margin:0;font-size:18px">Kuziini - Mesaj nou</h2>
      </div>
      <div style="background:#f8f7ff;padding:20px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px">
        <p style="margin:0 0 8px"><b>De la:</b> {sender_name}</p>
        {'<p style="margin:0 0 8px"><b>Oferta:</b> #' + offer_ref + '</p>' if offer_ref else ''}
        {'<p style="margin:0 0 8px"><b>Catre:</b> ' + ', '.join(recipients) + '</p>' if recipients else ''}
        <div style="background:#fff;padding:12px 16px;border-radius:8px;border-left:4px solid #7c3aed;margin-top:12px">
          <p style="margin:0;color:#333;white-space:pre-wrap">{preview}</p>
        </div>
        <p style="margin:16px 0 0;font-size:12px;color:#6b7280">
          Deschide <a href="https://kuziini.app" style="color:#7c3aed">kuziini.app</a> pentru a raspunde.
        </p>
      </div>
    </div>
    '''

    sent = False
    targets = recipients or []
    for username in targets:
        if username == sender_username:
            continue  # don't notify sender
        email = user_emails.get(username)
        if email:
            sent = send_email(email, subject, html) or sent

    # Also notify admin if configured and not already a recipient
    admin_email = user_emails.get('_admin')
    if admin_email and sender_username != '_admin':
        sent = send_email(admin_email, subject, html) or sent

    return sent


def notify_offer_action(action, agent_name, agent_username, offer=None):
    """Send email notification for offer actions (save, export, etc.)."""
    user_emails = _get_user_emails()
    admin_email = user_emails.get('_admin')
    if not admin_email:
        return False

    labels = {
        'offer_save': 'Oferta salvata',
        'export_excel': 'Export Excel',
        'export_pdf': 'Print / PDF',
        'whatsapp_share': 'Trimis pe WhatsApp',
    }
    label = labels.get(action, action)
    subject = f'Kuziini - {label} de {agent_name}'

    offer_info = ''
    if offer:
        if offer.get('num'):
            offer_info += f'<p style="margin:4px 0"><b>Oferta:</b> {offer["num"]}</p>'
        if offer.get('client'):
            offer_info += f'<p style="margin:4px 0"><b>Client:</b> {offer["client"]}</p>'
        if offer.get('total'):
            try:
                offer_info += f'<p style="margin:4px 0"><b>Total:</b> {float(offer["total"]):.2f} Lei</p>'
            except (TypeError, ValueError):
                pass

    html = f'''
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px">
      <div style="background:linear-gradient(135deg,#1e1b4b,#3b0764);color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">
        <h2 style="margin:0;font-size:18px">Kuziini - {label}</h2>
      </div>
      <div style="background:#f8f7ff;padding:20px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px">
        <p style="margin:0 0 8px"><b>Agent:</b> {agent_name} ({agent_username})</p>
        {offer_info}
        <p style="margin:16px 0 0;font-size:12px;color:#6b7280">
          <a href="https://kuziini.app" style="color:#7c3aed">kuziini.app</a>
        </p>
      </div>
    </div>
    '''

    return send_email(admin_email, subject, html)
