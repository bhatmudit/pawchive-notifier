from __future__ import annotations
import html,os,re
from datetime import datetime
from typing import Any
import requests

RESEND_URL="https://api.resend.com/emails"
class EmailError(RuntimeError): pass

def _strip_html(value):
    value=re.sub(r"<br\s*/?>","\n",value,flags=re.I)
    value=re.sub(r"</p\s*>","\n",value,flags=re.I)
    value=re.sub(r"<[^>]+>"," ",value)
    return re.sub(r"\s+"," ",html.unescape(value)).strip()

def _format_date(value):
    if not value:return "Unknown"
    try:return datetime.fromisoformat(value.replace("Z","+00:00")).strftime("%B %-d, %Y %H:%M UTC")
    except (ValueError,TypeError):return value

def _post_url(c,p): return f"https://pawchive.pw/{c['service']}/user/{c['id']}/post/{p['id']}"
def _wrap_html(x): return f'<!doctype html><html><body style="font-family:Arial,sans-serif;line-height:1.5;color:#222">{x}</body></html>'

def build_email(notifications,preview_chars=300,startup_notice=False):
    new=sum(len(p) for _,p,k in notifications if k=="new")
    edited=sum(len(p) for _,p,k in notifications if k=="edited")
    total=new+edited
    if len(notifications)==1:
        c,p,k=notifications[0]; subject_body=f"{c['name']} — {len(p)} {'new' if k=='new' else 'edited'} post{'s' if len(p)!=1 else ''}"
    else:
        bits=([f"{new} new"] if new else[])+([f"{edited} edited"] if edited else[])
        subject_body=" · ".join(bits)+f" post{'s' if total!=1 else ''}"
    sections=[]; text=[]
    if startup_notice:
        sections.append("<p style='color:#0a7d32'><strong>✓ Notifier is up and running.</strong> This is the first email it has sent.</p>")
        text.append("✓ Notifier is up and running. This is the first email it has sent.\n")
    for c,posts,kind in notifications:
        if not posts: continue
        label="new" if kind=="new" else "edited"
        sections.append(f"<h2>{html.escape(c['name'])} <small>({html.escape(c['service'].upper())}) — {len(posts)} {label}</small></h2>")
        text.append(f"{c['name']} ({c['service'].upper()}) — {len(posts)} {label}")
        for p in sorted(posts,key=lambda x:x.get("published") or x.get("added") or ""):
            title=str(p.get("title") or "Untitled post"); url=_post_url(c,p)
            date=_format_date(p.get("published") or p.get("added"))
            full=_strip_html(str(p.get("content") or "")); preview=full[:preview_chars]
            badge="" if kind=="new" else " <span style='color:#996600'>(edited)</span>"
            sections.append("<article>"+f"<h3>{html.escape(title)}{badge}</h3><p><strong>Published:</strong> {html.escape(date)}</p>"+
                            (f"<p>{html.escape(preview)}{'…' if len(full)>preview_chars else ''}</p>" if preview else "")+
                            f"<p><a href='{html.escape(url,quote=True)}'>View on Pawchive →</a></p></article>")
            text += ["",title+(" (edited)" if kind=="edited" else ""),f"Published: {date}",f"View on Pawchive: {url}"]
    return "[Pawchive] "+subject_body,_wrap_html(f"<h1>Pawchive Updates — {total} post{'s' if total!=1 else ''}</h1>"+''.join(sections)),"\n".join(text)

def build_status_email(kind: str, **context: Any):
    if kind == "startup":
        creators = context["creators"]
        items = "".join(
            f"<li>{html.escape(c['name'])} ({html.escape(c['service'])})</li>"
            for c in creators
        )
        text_items = "\n".join(
            f"- {c['name']} ({c['service']})" for c in creators
        )
        return (
            "[Pawchive] Notifier is up and running",
            _wrap_html(
                "<h1>✓ Pawchive Notifier is running</h1>"
                "<p>Setup is complete. You will only hear from it again "
                "when there's something to report "
                "(or, if enabled, an occasional heartbeat).</p>"
                f"<p><strong>Monitoring {len(creators)} creator(s):</strong></p>"
                f"<ul>{items}</ul>"
            ),
            "Pawchive Notifier is running.\n\n"
            f"Monitoring {len(creators)} creator(s):\n{text_items}\n\n"
            "You will only hear from it again when there's something to report.",
        )

    if kind == "heartbeat":
        return (
            "[Pawchive] Still watching 👀",
            _wrap_html(
                "<h1>Still watching 👀</h1>"
                f"<p>Monitoring {context['creator_count']} creator(s), "
                f"{context['total_runs']} runs so far.</p>"
                f"<p>Last new post: "
                f"{html.escape(context.get('last_digest_at') or 'none yet')}</p>"
                "<p>No news is good news — this is just a periodic check-in.</p>"
            ),
            "Still watching.\n\n"
            f"Monitoring {context['creator_count']} creator(s), "
            f"{context['total_runs']} runs so far.\n"
            f"Last new post: {context.get('last_digest_at') or 'none yet'}",
        )

    if kind == "alert":
        failed = context["failed"]
        items = "".join(
            f"<li>{html.escape(name)}</li>" for name in failed
        )
        text_items = "\n".join(f"- {name}" for name in failed)
        return (
            "[Pawchive] ⚠ Creator fetch failure",
            _wrap_html(
                "<h1 style='color:#b00020'>⚠ Fetch failure</h1>"
                "<p>The following creator(s) have just started failing:</p>"
                f"<ul>{items}</ul>"
                "<p>They will keep being retried automatically. "
                "You will not receive repeated alerts while the failure continues.</p>"
            ),
            "Fetch failure.\n\n"
            f"Creators that just started failing:\n{text_items}\n\n"
            "They will be retried automatically.",
        )

    if kind == "recovered":
        recovered = context["recovered"]
        items = "".join(
            f"<li>{html.escape(name)}</li>" for name in recovered
        )
        text_items = "\n".join(f"- {name}" for name in recovered)
        return (
            "[Pawchive] ✓ Creator recovered",
            _wrap_html(
                "<h1 style='color:#0a7d32'>✓ Back to normal</h1>"
                "<p>The following creator(s) are fetching successfully again:</p>"
                f"<ul>{items}</ul>"
            ),
            "Creator recovery.\n\n"
            f"Creators back to normal:\n{text_items}",
        )

    raise ValueError(f"unknown status email kind: {kind}")

def send_email(*,subject,html_body,text_body,api_key=None,recipient=None,sender=None,timeout=30):
    api_key=api_key or os.environ.get("RESEND_API_KEY")
    recipient=recipient or os.environ.get("NOTIFICATION_EMAIL")
    sender=sender or os.environ.get("RESEND_FROM_EMAIL","Pawchive Notifier <onboarding@resend.dev>")
    if not api_key: raise EmailError("RESEND_API_KEY is not set")
    if not recipient: raise EmailError("NOTIFICATION_EMAIL is not set")
    try:
        r=requests.post(RESEND_URL,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},
                        json={"from":sender,"to":[recipient],"subject":subject,"html":html_body,"text":text_body},timeout=timeout)
    except requests.RequestException as e: raise EmailError(f"Resend request failed: {e}") from e
    if r.status_code>=300: raise EmailError(f"Resend returned HTTP {r.status_code}: {r.text[:500]}")
