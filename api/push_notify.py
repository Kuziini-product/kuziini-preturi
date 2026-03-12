"""
Web Push notification helper for Kuziini.
Uses pywebpush + VAPID for sending push notifications to subscribed browsers.
"""
import json
import os

VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS = {'sub': 'mailto:kuziini@kuziini.app'}


def _get_subscriptions():
    """Load all push subscriptions from Redis."""
    try:
        import auth_utils
        raw = auth_utils._rc('LRANGE', 'push_subscriptions', 0, -1) or []
        subs = []
        for r in raw:
            try:
                subs.append(json.loads(r) if isinstance(r, str) else r)
            except Exception:
                pass
        return subs
    except Exception:
        return []


def save_subscription(username, subscription_info):
    """Save a push subscription for a user."""
    try:
        import auth_utils
        # Remove old subs for this user first
        remove_subscription(username)
        entry = {'username': username, 'sub': subscription_info}
        auth_utils._rc('RPUSH', 'push_subscriptions', json.dumps(entry, ensure_ascii=False))
        return True
    except Exception:
        return False


def remove_subscription(username):
    """Remove push subscriptions for a user."""
    try:
        import auth_utils
        subs = _get_subscriptions()
        # Clear and re-add without this user
        if subs:
            auth_utils._rc('DEL', 'push_subscriptions')
            for s in subs:
                if s.get('username') != username:
                    auth_utils._rc('RPUSH', 'push_subscriptions',
                                   json.dumps(s, ensure_ascii=False))
        return True
    except Exception:
        return False


def send_push(title, body, url='/', tag='kuziini', target_usernames=None):
    """Send push notification to subscribed users.
    If target_usernames is None, sends to all subscribers.
    """
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return False

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return False

    subs = _get_subscriptions()
    if not subs:
        return False

    payload = json.dumps({
        'title': title,
        'body': body,
        'icon': '/icon-192.png',
        'url': url,
        'tag': tag,
    })

    sent = False
    for entry in subs:
        uname = entry.get('username', '')
        sub_info = entry.get('sub')
        if not sub_info:
            continue
        if target_usernames is not None and uname not in target_usernames:
            continue
        try:
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
                timeout=10
            )
            sent = True
        except Exception:
            # Subscription might be expired, remove it
            try:
                remove_subscription(uname)
            except Exception:
                pass

    return sent


def notify_chat_push(sender_name, sender_username, text, recipients=None, offer_ref=None):
    """Send push for new chat message."""
    title = f'Mesaj de la {sender_name}'
    if offer_ref:
        title += f' (#{offer_ref})'
    body = text[:200]
    targets = [r for r in (recipients or []) if r != sender_username]
    # Also notify admin
    targets.append('admin')
    send_push(title, body, url='/', tag=f'chat-{sender_username}', target_usernames=targets)


def notify_offer_push(action, agent_name, agent_username, offer=None):
    """Send push for offer actions."""
    labels = {
        'offer_save': 'Oferta salvata',
        'export_excel': 'Export Excel',
        'export_pdf': 'Print / PDF',
    }
    label = labels.get(action, action)
    title = f'Kuziini - {label}'
    body = f'{agent_name}'
    if offer and offer.get('num'):
        body += f' - #{offer["num"]}'
    if offer and offer.get('client'):
        body += f' ({offer["client"]})'
    # Notify admin (not the agent themselves)
    targets = ['admin'] if agent_username != 'admin' else None
    send_push(title, body, url='/', tag=f'offer-{action}', target_usernames=targets)
