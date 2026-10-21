"""临时脚本：清理旧 Release 并发布当前版本。用完即删。"""
import base64
import glob
import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://124.221.183.24:3000"
REPO = "xiaoen/codepoint"
TAG = "v1.6.4"
NAME = "v1.6.4 — 误按菜单 / 粘贴写入 / 预览窗与生效范围"
BODY = """这一版修了三件会"把代码写歪、把程序关掉"的事：

**1. 补全时误按 Alt（会打开别人的菜单栏，甚至把程序关掉）**
- 清修饰键残留时会给 Alt 补一对 down+up，而单独按一下 Alt = 激活窗口菜单栏；
  之后注入的 Shift+方向键在菜单里挪选中项，回车就成了"点击那一项"。
  现在 Alt / Win 一个按下都不发，只抬不按。
- 新增三道注入闸门：窗口换了不发、不在「生效范围」不发、菜单开着/焦点在工具条
  上不发。AI 补全默认只在生效范围列表内的程序里可用（设置里可单独放开）。

**2. 逐字符写入被"边敲边联想"插一脚，写进去的代码是歪的**
- 新增「写入方式」：默认整段粘贴（剪贴板 + Ctrl+V + 还原），动作只有一个。
  AI 结果哪怕一行也粘贴；快捷输入多行模板走粘贴、`:=` 这种短的仍逐字符；
  标点替换保持原样逐字符。粘贴前会让剪贴板净化器闭嘴，避免注释里的中文标点被改写。

**3. 关掉「Tab 补全提示」后 AI 预览也一起消失，结果还被作废**
- 两个开关各管各的：呼吸灯只管快捷输入的提示条（关掉它快捷输入照样能 Tab 补全），
  「显示预览窗」管 AI 预览（关掉它就生成完直接插入）。

测试 483 个通过。下载 `CodePoint-1.6.4.exe` 直接运行（单文件免安装），
升级前先从托盘退出旧程序。
"""

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def creds():
    text = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=http\nhost=124.221.183.24:3000\npath=%s.git\n\n" % REPO,
        capture_output=True, text=True, timeout=20,
    ).stdout
    user = password = ""
    for line in text.splitlines():
        if line.startswith("username="):
            user = line.split("=", 1)[1]
        elif line.startswith("password="):
            password = line.split("=", 1)[1]
    if not (user and password):
        sys.exit("拿不到凭证")
    token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
    return "Basic " + token


AUTH = creds()


def api(method, path, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", AUTH)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with OPENER.open(req, timeout=120) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw.strip().startswith((b"{", b"[")) else raw)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:300].decode("utf-8", "replace")


def upload(release_id, path):
    boundary = "----CodePointBoundary7d31"
    fname = os.path.basename(path)
    mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        blob = fh.read()
    body = b"".join([
        ("--%s\r\nContent-Disposition: form-data; name=\"attachment\"; filename=\"%s\"\r\n"
         "Content-Type: %s\r\n\r\n" % (boundary, fname, mime)).encode(),
        blob,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    req = urllib.request.Request(
        "%s/api/v1/repos/%s/releases/%s/assets?name=%s"
        % (BASE, REPO, release_id, urllib.parse.quote(fname)),
        data=body, method="POST")
    req.add_header("Authorization", AUTH)
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    try:
        with OPENER.open(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:300].decode("utf-8", "replace")


def main():
    code, data = api("GET", "/api/v1/repos/%s/releases" % REPO)
    if code != 200:
        sys.exit("列 Release 失败 %s %s" % (code, data))
    print("现有 Release：")
    for rel in data:
        print("  id=%s tag=%s" % (rel["id"], rel["tag_name"]))
    for rel in data:
        if rel["tag_name"] == TAG:
            continue
        code, out = api("DELETE", "/api/v1/repos/%s/releases/%s" % (REPO, rel["id"]))
        print("删除 %s -> %s" % (rel["tag_name"], code if code != 200 else 204))
        if code not in (200, 204):
            print("  ", out)

    code, data = api("GET", "/api/v1/repos/%s/releases" % REPO)
    target = next((r for r in data if r["tag_name"] == TAG), None)
    if target is None:
        code, target = api("POST", "/api/v1/repos/%s/releases" % REPO, {
            "tag_name": TAG, "name": NAME, "body": BODY,
            "draft": False, "prerelease": False,
        })
        if code not in (200, 201):
            sys.exit("创建 Release 失败 %s %s" % (code, target))
        print("已创建 Release %s (id=%s)" % (TAG, target["id"]))
    else:
        print("Release %s 已存在 (id=%s)" % (TAG, target["id"]))

    have = {a["name"] for a in (target.get("assets") or [])}
    for path in sorted(glob.glob("dist/CodePoint-1.6.4.exe")):
        fname = os.path.basename(path)
        if fname in have:
            print("附件已存在：", fname)
            continue
        code, out = upload(target["id"], path)
        if code in (200, 201):
            print("已上传 %s (%.2f MB)" % (fname, os.path.getsize(path) / 1048576.0))
        else:
            sys.exit("上传失败 %s %s" % (code, out))

    code, data = api("GET", "/api/v1/repos/%s/releases" % REPO)
    print("\n最终状态：")
    for rel in data:
        print("  %s  %s" % (rel["tag_name"], rel["name"]))
        for a in rel.get("assets") or []:
            print("      %s  %.2f MB  %s" % (a["name"], a["size"] / 1048576.0,
                                             a["browser_download_url"]))


if __name__ == "__main__":
    main()
