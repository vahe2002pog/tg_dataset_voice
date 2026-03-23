import hashlib
import io
import os
import secrets
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp_session
from aiohttp import web
from aiohttp_session.cookie_storage import EncryptedCookieStorage

DATASET_DIR = Path("dataset")
WEB_LOGIN = os.getenv("WEB_LOGIN", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "admin")


def count_wav(directory: Path) -> int:
    return len(list(directory.glob("*.wav")))


def get_stats() -> dict:
    if not DATASET_DIR.exists():
        return {"total_users": 0, "total_samples": 0, "per_user": []}

    per_user = []
    total = 0
    tz_plus5 = timezone(timedelta(hours=5))
    for d in sorted(DATASET_DIR.iterdir()):
        if not d.is_dir():
            continue
        c = count_wav(d)
        if c > 0:
            # Get the latest modification time from all .wav files
            wav_files = list(d.glob("*.wav"))
            if wav_files:
                latest_mtime = max(f.stat().st_mtime for f in wav_files)
                latest_time = datetime.fromtimestamp(latest_mtime, tz=tz_plus5).strftime("%Y-%m-%d %H:%M")
            else:
                latest_time = "—"

            per_user.append({"user_id": d.name, "samples": c, "added_at": latest_time})
            total += c
    return {"total_users": len(per_user), "total_samples": total, "per_user": per_user}


async def is_authenticated(request: web.Request) -> bool:
    session = await aiohttp_session.get_session(request)
    return session.get("authenticated", False)


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login — Dataset Admin</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0f0f0f; color: #e0e0e0; display: flex;
               align-items: center; justify-content: center; min-height: 100vh; }
        .login-card { background: #1a1a2e; border-radius: 16px; padding: 2.5rem;
                      width: 100%; max-width: 380px; }
        .login-card h1 { font-size: 1.3rem; margin-bottom: 1.5rem; color: #fff; text-align: center; }
        .field { margin-bottom: 1rem; }
        .field label { display: block; font-size: 0.85rem; color: #888; margin-bottom: 0.4rem; }
        .field input { width: 100%; padding: 0.7rem 1rem; border-radius: 8px; border: 1px solid #333;
                       background: #0f0f0f; color: #e0e0e0; font-size: 1rem; outline: none; }
        .field input:focus { border-color: #7c3aed; }
        .btn { width: 100%; padding: 0.8rem; border: none; border-radius: 8px; background: #7c3aed;
               color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; margin-top: 0.5rem; }
        .btn:hover { background: #6d28d9; }
        .error { color: #ef4444; font-size: 0.85rem; text-align: center; margin-bottom: 1rem; }
    </style>
</head>
<body>
    <div class="login-card">
        <h1>Dataset Admin</h1>
        {error}
        <form method="POST" action="/login">
            <div class="field">
                <label>Логин</label>
                <input type="text" name="login" autocomplete="username" required autofocus>
            </div>
            <div class="field">
                <label>Пароль</label>
                <input type="password" name="password" autocomplete="current-password" required>
            </div>
            <button class="btn" type="submit">Войти</button>
        </form>
    </div>
</body>
</html>"""


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dataset Admin</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0f0f0f; color: #e0e0e0; padding: 2rem; }}
        .container {{ max-width: 700px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; }}
        h1 {{ font-size: 1.5rem; color: #fff; }}
        .logout {{ color: #888; text-decoration: none; font-size: 0.85rem; }}
        .logout:hover {{ color: #ef4444; }}
        .stats {{ display: flex; gap: 1rem; margin-bottom: 2rem; }}
        .stat-card {{ background: #1a1a2e; border-radius: 12px; padding: 1.2rem;
                      flex: 1; text-align: center; }}
        .stat-card .number {{ font-size: 2rem; font-weight: 700; color: #7c3aed; }}
        .stat-card .label {{ font-size: 0.85rem; color: #888; margin-top: 0.3rem; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 2rem; }}
        th, td {{ padding: 0.7rem 1rem; text-align: left; border-bottom: 1px solid #222; }}
        th {{ color: #888; font-weight: 500; font-size: 0.85rem; text-transform: uppercase; }}
        .time {{ color: #666; font-size: 0.9rem; }}
        table a {{ color: #7c3aed; text-decoration: none; transition: color 0.2s; }}
        table a:hover {{ color: #a78bfa; text-decoration: underline; }}
        .btn {{ display: inline-block; background: #7c3aed; color: #fff; padding: 0.8rem 2rem;
                border-radius: 8px; text-decoration: none; font-weight: 600; transition: background 0.2s; }}
        .btn:hover {{ background: #6d28d9; }}
        .empty {{ color: #666; text-align: center; padding: 2rem; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Dataset Admin</h1>
            <a class="logout" href="/logout">Выйти</a>
        </div>
        <div class="stats">
            <div class="stat-card">
                <div class="number">{total_users}</div>
                <div class="label">Пользователей</div>
            </div>
            <div class="stat-card">
                <div class="number">{total_samples}</div>
                <div class="label">Сэмплов</div>
            </div>
        </div>
        {table}
        {download_btn}
    </div>
</body>
</html>"""


async def handle_login_page(request: web.Request):
    if await is_authenticated(request):
        raise web.HTTPFound("/dataset")
    html = LOGIN_PAGE.replace("{error}", "")
    return web.Response(text=html, content_type="text/html")


async def handle_login(request: web.Request):
    data = await request.post()
    login = data.get("login", "")
    password = data.get("password", "")

    if login == WEB_LOGIN and password == WEB_PASSWORD:
        session = await aiohttp_session.get_session(request)
        session["authenticated"] = True
        raise web.HTTPFound("/dataset")

    html = LOGIN_PAGE.replace("{error}", '<p class="error">Неверный логин или пароль</p>')
    return web.Response(text=html, content_type="text/html")


async def handle_logout(request: web.Request):
    session = await aiohttp_session.get_session(request)
    session.invalidate()
    raise web.HTTPFound("/login")


async def handle_index(request: web.Request):
    if not await is_authenticated(request):
        raise web.HTTPFound("/login")

    stats = get_stats()

    rows = ""
    for i, u in enumerate(stats["per_user"], 1):
        user_id = u['user_id']
        rows += f"<tr><td>{i}</td><td><a href='/user/{user_id}/files'>{user_id}</a></td><td>{u['samples']}</td><td class='time'>{u['added_at']}</td></tr>"

    table = (
        "<table><tr><th>#</th><th>User ID</th><th>Сэмплов</th><th>Последнее добавление</th></tr>" + rows + "</table>"
        if rows
        else '<p class="empty">Пока нет записей</p>'
    )
    download_btn = (
        '<a class="btn" href="/download">Скачать архив (.zip)</a>'
        if stats["total_samples"] > 0
        else ""
    )

    html = DASHBOARD_TEMPLATE.format(
        total_users=stats["total_users"],
        total_samples=stats["total_samples"],
        table=table,
        download_btn=download_btn,
    )
    return web.Response(text=html, content_type="text/html")


async def handle_download(request: web.Request):
    if not await is_authenticated(request):
        raise web.HTTPFound("/login")

    if not DATASET_DIR.exists():
        return web.Response(text="Dataset is empty", status=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for wav_file in DATASET_DIR.rglob("*.wav"):
            zf.write(wav_file, wav_file.relative_to(DATASET_DIR))
    buf.seek(0)

    return web.Response(
        body=buf.read(),
        headers={
            "Content-Disposition": "attachment; filename=dataset.zip",
            "Content-Type": "application/zip",
        },
    )


async def handle_user_files(request: web.Request):
    if not await is_authenticated(request):
        raise web.HTTPFound("/login")

    user_id = request.match_info.get("user_id", "")
    if not user_id:
        raise web.HTTPFound("/")

    user_dir = DATASET_DIR / user_id
    if not user_dir.exists():
        raise web.HTTPFound("/")

    wav_files = sorted(user_dir.glob("*.wav"))
    files_html = ""
    for wav_file in wav_files:
        file_name = wav_file.name
        file_url = f"/audio/{user_id}/{file_name}"
        files_html += f"""
        <div style="background: #1a1a2e; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
            <div style="margin-bottom: 0.5rem; font-weight: 500;">{file_name}</div>
            <audio controls style="width: 100%; max-width: 400px;">
                <source src="{file_url}" type="audio/wav">
                Ваш браузер не поддерживает audio элемент.
            </audio>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Files — User {user_id}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0f0f0f; color: #e0e0e0; padding: 2rem; }}
        .container {{ max-width: 700px; margin: 0 auto; }}
        .header {{ margin-bottom: 2rem; }}
        .header h1 {{ font-size: 1.5rem; color: #fff; margin-bottom: 0.5rem; }}
        .back-link {{ color: #888; text-decoration: none; font-size: 0.9rem; }}
        .back-link:hover {{ color: #7c3aed; }}
        audio {{ background: #0f0f0f; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <a class="back-link" href="/dataset">← Назад</a>
            <h1>Файлы пользователя {user_id}</h1>
        </div>
        {files_html if files_html else '<p style="color: #666;">Нет файлов</p>'}
    </div>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def handle_audio(request: web.Request):
    if not await is_authenticated(request):
        raise web.HTTPFound("/login")

    user_id = request.match_info.get("user_id", "")
    file_name = request.match_info.get("file_name", "")

    if not user_id or not file_name:
        raise web.HTTPNotFound()

    file_path = DATASET_DIR / user_id / file_name

    # Security check: ensure path is within user directory
    try:
        file_path.resolve().relative_to(DATASET_DIR.resolve())
    except ValueError:
        raise web.HTTPForbidden()

    if not file_path.exists() or not file_path.is_file():
        raise web.HTTPNotFound()

    return web.FileResponse(str(file_path))


def create_app() -> web.Application:
    # Generate a stable secret key from the password
    secret = hashlib.sha256(WEB_PASSWORD.encode()).digest()
    storage = EncryptedCookieStorage(secret)

    app = web.Application()
    aiohttp_session.setup(app, storage)

    app.router.add_get("/login", handle_login_page)
    app.router.add_post("/login", handle_login)
    app.router.add_get("/logout", handle_logout)
    app.router.add_get("/", handle_index)
    app.router.add_get("/download", handle_download)
    app.router.add_get("/user/{user_id}/files", handle_user_files)
    app.router.add_get("/audio/{user_id}/{file_name}", handle_audio)
    return app
