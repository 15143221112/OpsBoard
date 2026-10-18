"""本地集成测试：验证 app-panel 后端全部接口"""
import requests, json, sys

BASE = "http://127.0.0.1:8085"
passed, failed = 0, 0

def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} {extra}")

print("== 1. health ==")
r = requests.get(f"{BASE}/api/health", timeout=5)
check("health", r.status_code == 200, r.text)

print("== 2. 登录 ==")
r = requests.post(f"{BASE}/api/auth/login", json={"username":"admin","password":"123456"}, timeout=5)
check("登录成功", r.status_code == 200, r.text)
token = r.json()["access_token"]
check("token非空", len(token) > 50)
H = {"Authorization": f"Bearer {token}"}

print("== 3. 错误密码 ==")
r = requests.post(f"{BASE}/api/auth/login", json={"username":"admin","password":"wrong"}, timeout=5)
check("错误密码被拒", r.status_code == 401)

print("== 4. 未登录访问 ==")
r = requests.get(f"{BASE}/api/apps", timeout=5)
check("无token被拒", r.status_code == 401)

print("== 5. 应用列表 ==")
r = requests.get(f"{BASE}/api/apps", headers=H, timeout=30)
check("应用列表", r.status_code == 200)
apps = r.json()
check("应用数=15", len(apps) == 15, f"got {len(apps)}")
fields = ["name","type","unit_name","port","status","mem_mb","gpu_mib","health","deploy_type","path","web_url"]
check("字段齐全", all(k in apps[0] for k in fields), json.dumps(apps[0])[:200])

print("== 6. 总览 ==")
r = requests.get(f"{BASE}/api/overview", headers=H, timeout=10)
check("总览", r.status_code == 200 and "gpu" in r.json())

print("== 7. 操作日志 ==")
r = requests.get(f"{BASE}/api/ops", headers=H, timeout=10)
check("操作日志", r.status_code == 200 and isinstance(r.json(), list))

print("== 8. 应用状态单查 ==")
r = requests.get(f"{BASE}/api/apps/1/status", headers=H, timeout=10)
check("单应用状态", r.status_code == 200)

print("== 9. 历史日志 ==")
r = requests.get(f"{BASE}/api/apps/1/logs?tail=20", headers=H, timeout=10)
check("历史日志接口", r.status_code == 200, r.text[:100])

print("== 10. 前端首页 ==")
r = requests.get(f"{BASE}/", timeout=5)
check("index.html可访问", r.status_code == 200 and "App Panel" in r.text)

print(f"\n结果: {passed} 通过 / {failed} 失败")
sys.exit(0 if failed == 0 else 1)
