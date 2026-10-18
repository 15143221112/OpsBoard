"""上传 app-panel 代码到服务器 /home/ubuntu 后 sudo 部署（内部脚本）

凭据通过环境变量注入，不写入代码：
  DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASS / DEPLOY_SUDO_PASS
"""
import os, paramiko

host = os.environ.get("DEPLOY_HOST", "")
user = os.environ.get("DEPLOY_USER", "")
pw = os.environ.get("DEPLOY_PASS", "")
SUDO_PASS = os.environ.get("DEPLOY_SUDO_PASS", pw)
LOCAL_ROOT = os.environ.get("DEPLOY_LOCAL_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(LOCAL_ROOT, "backend")
DEPLOY = os.path.join(LOCAL_ROOT, "deploy")

if not host or not user or not pw:
    raise SystemExit("请设置环境变量 DEPLOY_HOST / DEPLOY_USER / DEPLOY_PASS")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, port=22, username=user, password=pw, timeout=20)
sftp = c.open_sftp()

def ensure_remote_dir(path):
    parts = path.strip("/").split("/")
    cur = "/"
    for p in parts:
        cur = cur.rstrip("/") + "/" + p
        try:
            sftp.stat(cur)
        except:
            sftp.mkdir(cur)

def upload_dir(local, remote):
    for root, dirs, files in os.walk(local):
        rel = os.path.relpath(root, local)
        target = remote if rel == "." else remote + "/" + rel.replace("\\", "/")
        ensure_remote_dir(target)
        for f in files:
            lp = os.path.join(root, f)
            rp = target + "/" + f
            sftp.put(lp, rp)
            print(f"  up {rp}")

def run(cmd, t=30):
    stdin,stdout,stderr=c.exec_command(cmd, timeout=t)
    return stdout.read().decode(errors='replace').strip() + stderr.read().decode(errors='replace').strip()

print("== 上传到 /home/ubuntu/app-panel-tmp ==")
ensure_remote_dir("/home/ubuntu/app-panel-tmp")
ensure_remote_dir("/home/ubuntu/app-panel-tmp/backend")
ensure_remote_dir("/home/ubuntu/app-panel-tmp/data")
upload_dir(BACKEND, "/home/ubuntu/app-panel-tmp/backend")
ensure_remote_dir("/home/ubuntu/app-panel-tmp/deploy")
upload_dir(DEPLOY, "/home/ubuntu/app-panel-tmp/deploy")

print("== sudo 部署 ==")
print(run("echo '%s' | sudo -S bash /home/ubuntu/app-panel-tmp/deploy/deploy.sh 2>&1 | grep -v password" % SUDO_PASS, t=180))

sftp.close(); c.close()
