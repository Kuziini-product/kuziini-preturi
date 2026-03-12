"""
Daily report generator for Kuziini.
Generates a summary email with all offers, chat, and Excel/PDF attachments.
Called by GitHub Actions cron or manually via /api/report/daily.
"""
import json
import base64
import time
import os


def _fmt(val):
    try:
        return f'{float(val):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except (TypeError, ValueError):
        return '0,00'


def _generate_offer_excel_html(offer):
    """Generate an Excel-compatible HTML table for an offer (same format as frontend)."""
    products = offer.get('products', [])
    discount = float(offer.get('discount', 0))
    num = offer.get('num', '')
    date = offer.get('date', '')
    client = offer.get('client', '')
    phone = offer.get('phone', '')
    email = offer.get('email', '')
    notes = offer.get('notes', '')
    description = offer.get('description', '')

    html = '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel">'
    html += '<head><meta charset="UTF-8"></head><body>'

    # Header
    html += '<table style="margin-bottom:6px;font-size:17px;font-family:Segoe UI,Arial,sans-serif;color:#111">'
    html += f'<tr><td colspan="6" style="font-size:24px;font-weight:bold;color:#3b0764;padding-bottom:4px">OFERTA COMERCIALA</td></tr>'
    html += f'<tr><td colspan="3"><b>Oferta:</b> {num}</td><td colspan="3"><b>Data:</b> {date}</td></tr>'
    html += f'<tr><td colspan="3"><b>Client:</b> {client or "—"}</td><td colspan="3"><b>Tel:</b> {phone or "—"}</td></tr>'
    if email:
        html += f'<tr><td colspan="6"><b>Email:</b> {email}</td></tr>'
    if notes:
        html += f'<tr><td colspan="6"><b>Observatii:</b> {notes}</td></tr>'
    html += '</table>'

    # Products table
    html += '<table border="1" style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;font-size:16px;width:100%;color:#111">'
    html += '<tr style="background:#1e1b4b;color:#fff;font-weight:bold;font-size:16px">'
    html += '<th style="padding:8px 6px;text-align:center">#</th>'
    html += '<th style="padding:8px 10px;text-align:left">Produs</th>'
    html += '<th style="padding:8px 10px;text-align:left">Categorie</th>'
    html += '<th style="padding:8px 6px;text-align:center">Cant.</th>'
    html += '<th style="padding:8px 10px;text-align:right">Pret Unitar (Lei)</th>'
    html += '<th style="padding:8px 10px;text-align:right">Subtotal (Lei)</th>'
    html += '</tr>'

    grand_total = 0
    subtotal_no_disc = 0
    total_items = 0
    for i, item in enumerate(products):
        base_price = float(item.get('baseSellPrice', 0))
        qty = int(item.get('qty', 1))
        sell = base_price * (1 - discount / 100)
        sub = sell * qty
        grand_total += sub
        subtotal_no_disc += base_price * qty
        total_items += qty
        bg = '#fff' if i % 2 == 0 else '#f5f3ff'
        code = item.get('code', '')
        cat = item.get('category', '')
        html += f'<tr style="background:{bg}">'
        html += f'<td style="padding:6px;text-align:center">{i+1}</td>'
        html += f'<td style="padding:6px 10px;font-weight:bold;color:#111">{code}</td>'
        html += f'<td style="padding:6px 10px;color:#374151">{cat}</td>'
        html += f'<td style="padding:6px;text-align:center">{qty}</td>'
        html += f'<td style="padding:6px 10px;text-align:right">{_fmt(sell)}</td>'
        html += f'<td style="padding:6px 10px;text-align:right;font-weight:bold">{_fmt(sub)}</td>'
        html += '</tr>'
    html += '</table>'

    # Totals
    html += '<table style="margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:17px;width:100%;color:#111">'
    html += f'<tr><td style="text-align:right;padding:4px 10px">Subtotal (fara discount):</td><td style="text-align:right;padding:4px 10px;width:140px">{_fmt(subtotal_no_disc)} Lei</td></tr>'
    if discount > 0:
        disc_amount = subtotal_no_disc - grand_total
        html += f'<tr style="color:#b91c1c;font-weight:bold"><td style="text-align:right;padding:4px 10px">Discount {discount:.1f}%:</td><td style="text-align:right;padding:4px 10px">-{_fmt(disc_amount)} Lei</td></tr>'
    html += f'<tr style="font-size:20px;font-weight:bold"><td style="text-align:right;padding:8px 10px;border-top:2px solid #1e1b4b">TOTAL OFERTA (cu TVA):</td><td style="text-align:right;padding:8px 10px;border-top:2px solid #1e1b4b;width:140px">{_fmt(grand_total)} Lei</td></tr>'
    plural = 'e' if total_items != 1 else ''
    html += f'<tr><td style="text-align:right;padding:2px 10px;color:#4b5563;font-size:14px">{total_items} produs{plural} · {len(products)} pozitii</td><td></td></tr>'
    html += '</table>'

    if description:
        html += f'<p style="margin-top:12px;font-size:16px;font-family:Segoe UI,Arial,sans-serif"><b>Descriere / Note aditionale:</b><br>{description}</p>'

    html += f'<p style="margin-top:16px;font-size:14px;color:#374151;font-family:Segoe UI,Arial,sans-serif"><b>Valabilitate oferta:</b> 7 zile calendaristice de la data emiterii. Preturile includ TVA. Stocul este limitat.</p>'
    html += f'<p style="font-size:14px;color:#374151;font-family:Segoe UI,Arial,sans-serif;text-align:right"><b>Kuziini</b> · kuziini.ro · Oferta nr. {num} · {date}</p>'
    html += '</body></html>'
    return html


def _generate_offer_pdf_html(offer, chat_messages=None):
    """Generate a print-ready HTML for an offer (acts as PDF)."""
    products = offer.get('products', [])
    discount = float(offer.get('discount', 0))
    num = offer.get('num', '')
    date = offer.get('date', '')
    client = offer.get('client', '')
    phone = offer.get('phone', '')
    email_addr = offer.get('email', '')
    description = offer.get('description', '')

    html = '''<html><head><meta charset="UTF-8">
    <style>
      body { font-family: Segoe UI, Arial, sans-serif; margin: 20px; color: #111; }
      table { border-collapse: collapse; width: 100%; }
      th, td { padding: 8px 10px; }
      .header { font-size: 24px; font-weight: bold; color: #3b0764; margin-bottom: 16px; }
      .info { font-size: 14px; margin-bottom: 4px; }
      .products th { background: #1e1b4b; color: #fff; font-weight: bold; font-size: 14px; }
      .products td { font-size: 13px; border-bottom: 1px solid #e5e7eb; }
      .total-row { font-size: 18px; font-weight: bold; border-top: 2px solid #1e1b4b; }
      .chat-section { margin-top: 20px; border-top: 2px solid #e5e7eb; padding-top: 12px; }
      .chat-msg { padding: 6px 10px; margin: 4px 0; border-left: 3px solid #7c3aed; background: #f8f7ff; border-radius: 4px; font-size: 12px; }
    </style></head><body>'''

    html += f'<div class="header">OFERTA COMERCIALA - {num}</div>'
    html += f'<div class="info"><b>Data:</b> {date} | <b>Client:</b> {client or "—"} | <b>Tel:</b> {phone or "—"}</div>'
    if email_addr:
        html += f'<div class="info"><b>Email:</b> {email_addr}</div>'

    # Products
    html += '<table class="products" style="margin-top:16px"><tr>'
    html += '<th style="text-align:center;width:30px">#</th><th style="text-align:left">Produs</th>'
    html += '<th style="text-align:left">Categorie</th><th style="text-align:center">Cant.</th>'
    html += '<th style="text-align:right">Pret Unitar</th><th style="text-align:right">Subtotal</th></tr>'

    grand_total = 0
    subtotal_no_disc = 0
    for i, item in enumerate(products):
        base_price = float(item.get('baseSellPrice', 0))
        qty = int(item.get('qty', 1))
        sell = base_price * (1 - discount / 100)
        sub = sell * qty
        grand_total += sub
        subtotal_no_disc += base_price * qty
        bg = '#fff' if i % 2 == 0 else '#f9f8ff'
        html += f'<tr style="background:{bg}"><td style="text-align:center">{i+1}</td>'
        html += f'<td style="font-weight:bold">{item.get("code","")}</td>'
        html += f'<td>{item.get("category","")}</td>'
        html += f'<td style="text-align:center">{qty}</td>'
        html += f'<td style="text-align:right">{_fmt(sell)} Lei</td>'
        html += f'<td style="text-align:right;font-weight:bold">{_fmt(sub)} Lei</td></tr>'

    html += '</table>'

    # Totals
    html += f'<div style="text-align:right;margin-top:12px;font-size:14px">Subtotal: {_fmt(subtotal_no_disc)} Lei</div>'
    if discount > 0:
        html += f'<div style="text-align:right;color:#b91c1c;font-weight:bold;font-size:14px">Discount {discount:.1f}%: -{_fmt(subtotal_no_disc - grand_total)} Lei</div>'
    html += f'<div style="text-align:right;font-size:20px;font-weight:bold;margin-top:8px;padding-top:8px;border-top:2px solid #1e1b4b">TOTAL: {_fmt(grand_total)} Lei</div>'

    if description:
        html += f'<div style="margin-top:16px;padding:12px;background:#f8f7ff;border-radius:8px;font-size:13px"><b>Note:</b><br>{description}</div>'

    # Chat messages
    if chat_messages:
        html += '<div class="chat-section"><div style="font-size:14px;font-weight:bold;color:#3b0764;margin-bottom:8px">Conversatie oferta</div>'
        for m in chat_messages:
            name = m.get('name', m.get('username', ''))
            text = m.get('text', '')
            ts = m.get('ts', 0)
            from datetime import datetime
            dt = datetime.fromtimestamp(ts).strftime('%d.%m %H:%M') if ts else ''
            html += f'<div class="chat-msg"><b>{name}</b> <span style="color:#9ca3af;font-size:10px">{dt}</span><br>{text}</div>'
        html += '</div>'

    html += '<div style="margin-top:20px;font-size:11px;color:#6b7280;text-align:right"><b>Kuziini</b> · kuziini.ro</div>'
    html += '</body></html>'
    return html


def generate_daily_report(date_filter=None):
    """
    Generate daily report data: summary HTML + attachments for all offers.
    date_filter: 'YYYY-MM-DD' to filter offers by date, or None for all today's offers.
    Returns (subject, html_body, attachments_list).
    """
    import auth_utils
    from datetime import datetime

    today = date_filter or datetime.now().strftime('%Y-%m-%d')
    today_display = datetime.now().strftime('%d.%m.%Y')

    # Get ALL offers
    all_ids = auth_utils._jget('offers:all') or []
    offers = []
    for oid in all_ids:
        o = auth_utils._jget(f'offer:{oid}')
        if o:
            # Filter by date if specified
            offer_date = o.get('date', '')
            # Match various date formats
            if date_filter:
                if offer_date and (today in offer_date or offer_date.replace('.', '-') == today):
                    offers.append(o)
                elif offer_date == today_display or offer_date == today.replace('-', '.'):
                    offers.append(o)
            else:
                offers.append(o)

    # Get user names map
    users_map = {}
    try:
        all_users = auth_utils.get_all_usernames()
        for uname in all_users:
            u = auth_utils.get_user(uname)
            if u:
                users_map[uname] = u.get('name', uname)
    except:
        pass

    # Build summary HTML
    subject = f'Kuziini - Raport Oferte {today_display}'
    if date_filter:
        subject += f' ({len(offers)} oferte)'

    summary_html = f'''
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:700px;margin:0 auto">
      <div style="background:linear-gradient(135deg,#1e1b4b,#3b0764);color:#fff;padding:20px 24px;border-radius:14px 14px 0 0">
        <h1 style="margin:0;font-size:22px">Raport Oferte Kuziini</h1>
        <p style="margin:6px 0 0;font-size:13px;opacity:.8">{today_display} · {len(offers)} oferte</p>
      </div>
      <div style="background:#f8f7ff;padding:20px 24px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 14px 14px">
    '''

    if not offers:
        summary_html += '<p style="color:#6b7280;text-align:center;padding:20px">Nu sunt oferte pentru aceasta perioada.</p>'
    else:
        # Summary table
        summary_html += '''<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px">
          <tr style="background:#1e1b4b;color:#fff">
            <th style="padding:10px 8px;text-align:left;border-radius:8px 0 0 0">Oferta</th>
            <th style="padding:10px 8px;text-align:left">Client</th>
            <th style="padding:10px 8px;text-align:left">Agent</th>
            <th style="padding:10px 8px;text-align:right">Total</th>
            <th style="padding:10px 8px;text-align:center">Produse</th>
            <th style="padding:10px 8px;text-align:center;border-radius:0 8px 0 0">Chat</th>
          </tr>'''

        total_revenue = 0
        for i, o in enumerate(offers):
            num = o.get('num', '?')
            client = o.get('client', '—')
            owner = o.get('owner_id', '')
            owner_name = users_map.get(owner, owner)
            total = float(o.get('total', 0)) if o.get('total') else 0
            total_revenue += total
            prods = o.get('products', [])
            prod_count = len(prods)
            qty_total = sum(p.get('qty', 1) for p in prods)

            # Get chat count
            oid = o.get('num') or o.get('id')
            try:
                chat_count = int(auth_utils._rc('LLEN', f'offer_chat:{oid}') or 0)
            except:
                chat_count = 0

            bg = '#fff' if i % 2 == 0 else '#f5f3ff'
            summary_html += f'''<tr style="background:{bg}">
              <td style="padding:8px;font-weight:bold;color:#3b0764">{num}</td>
              <td style="padding:8px">{client}</td>
              <td style="padding:8px">{owner_name}</td>
              <td style="padding:8px;text-align:right;font-weight:bold">{_fmt(total)} Lei</td>
              <td style="padding:8px;text-align:center">{prod_count} ({qty_total} buc)</td>
              <td style="padding:8px;text-align:center">{chat_count if chat_count > 0 else "—"}</td>
            </tr>'''

        summary_html += f'''<tr style="background:#1e1b4b;color:#fff;font-weight:bold">
          <td colspan="3" style="padding:10px 8px;border-radius:0 0 0 8px">TOTAL ({len(offers)} oferte)</td>
          <td style="padding:10px 8px;text-align:right;border-radius:0 0 0 0">{_fmt(total_revenue)} Lei</td>
          <td colspan="2" style="padding:10px 8px;border-radius:0 0 8px 0"></td>
        </tr></table>'''

    summary_html += '''
        <p style="font-size:11px;color:#6b7280;text-align:center;margin-top:16px">
          Fisierele Excel si PDF sunt atasate acestui email.<br>
          <a href="https://kuziini.app" style="color:#7c3aed">kuziini.app</a>
        </p>
      </div>
    </div>'''

    # Generate attachments
    attachments = []
    for o in offers:
        num = o.get('num', 'oferta')
        oid = o.get('num') or o.get('id')
        safe_name = str(num).replace('/', '-').replace('\\', '-')

        # Excel attachment
        excel_html = _generate_offer_excel_html(o)
        excel_b64 = base64.b64encode(excel_html.encode('utf-8')).decode('ascii')
        attachments.append({
            'filename': f'{safe_name}.xls',
            'content': excel_b64,
            'content_type': 'application/vnd.ms-excel',
        })

        # PDF-ready HTML attachment
        chat_msgs = []
        try:
            chat_msgs = auth_utils.get_offer_chat(oid, o)
        except:
            pass
        pdf_html = _generate_offer_pdf_html(o, chat_msgs)
        pdf_b64 = base64.b64encode(pdf_html.encode('utf-8')).decode('ascii')
        attachments.append({
            'filename': f'{safe_name}.html',
            'content': pdf_b64,
            'content_type': 'text/html',
        })

    return subject, summary_html, attachments


def send_daily_report(to_email=None, date_filter=None):
    """Generate and send the daily report email."""
    import email_notify

    if not to_email:
        # Get admin email from settings
        try:
            import auth_utils
            s = auth_utils.get_app_settings()
            emails = s.get('notify_emails', {})
            to_email = emails.get('_admin') or emails.get(next(iter(emails), ''), '')
        except:
            pass

    if not to_email:
        return False, 'Nu este configurat niciun email.'

    subject, html_body, attachments = generate_daily_report(date_filter)

    # Use Resend API with attachments
    api_key = os.environ.get('RESEND_API_KEY', '')
    if not api_key:
        return False, 'RESEND_API_KEY nu este configurat.'

    import urllib.request
    try:
        payload = json.dumps({
            'from': email_notify.FROM_EMAIL,
            'to': [to_email],
            'subject': subject,
            'html': html_body,
            'attachments': attachments,
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201), 'Raport trimis cu succes!'
    except Exception as e:
        return False, f'Eroare: {e}'
