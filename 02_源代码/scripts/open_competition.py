"""Start the four-page application using resources inside this download."""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import webbrowser
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))


def prepare_workspace(root):
    """Install the read-only seed once; existing user data is never overwritten."""
    root = root.resolve()
    if not root.is_relative_to((ROOT / 'artifacts/eng063').resolve()):
        raise ValueError('工作区须位于本程序 artifacts/eng063 目录内')
    root.mkdir(parents=True, exist_ok=True)
    destination = root / 'workbench.sqlite3'
    if destination.exists(): return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix='.sqlite3', delete=False) as output:
            temporary = Path(output.name)
            with gzip.open(ROOT / 'data/示例事件库.sqlite3.gz', 'rb') as stream:
                shutil.copyfileobj(stream, output)
        expected = json.loads((ROOT / 'data/seed-manifest.json').read_text('utf8'))['database_sha256']
        with temporary.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('示例数据文件损坏，请重新下载')
        # Windows rename also works on USB disks and cannot replace an existing DB.
        try:
            if os.name == 'nt': os.rename(temporary, destination)
            else: os.link(temporary, destination)
        except FileExistsError: pass
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def main():
    if sys.version_info < (3, 12): raise SystemExit('需要 Python 3.12 或更新版本，或使用随包提供的 Windows 启动入口。')
    try:
        config_path = ROOT / 'config/deepseek.json'
        config = json.loads(config_path.read_text('utf-8-sig')) if config_path.exists() else {}
        if not isinstance(config, dict): raise ValueError()
    except (OSError, ValueError): raise SystemExit('模型配置文件无法读取，请检查 config/deepseek.json。') from None
    p = argparse.ArgumentParser(description='见微 · 上市公司与行业事件研究系统')
    p.add_argument('--port', type=int, default=config.get('port', 8780))
    p.add_argument('--cases-root', type=Path, default=ROOT / 'artifacts/eng063/local')
    p.add_argument('--case-run', default='training-showcase')
    p.add_argument('--no-browser', action='store_true')
    p.add_argument('--offline', action='store_true', help='浏览案例、保存材料和个人操作，不调用模型')
    p.add_argument('--send-model', action='store_true')
    p.add_argument('--model-budget-ledger', type=Path, default=ROOT / 'artifacts/eng063/local/model-budget.sqlite3')
    p.add_argument('--model-budget-rmb', type=Decimal, default=Decimal(str(config.get('model_budget_rmb', '3'))))
    args = p.parse_args()
    args.send_model = not args.offline and (args.send_model or config.get('send_model', True))
    if not 1 <= args.port <= 65535: raise SystemExit('端口必须在 1—65535 之间。')
    from urllib.parse import urlencode
    from urllib.request import urlopen
    url = f'http://127.0.0.1:{args.port}/?' + urlencode(dict(mode='real', run=args.case_run))
    identity = hashlib.sha256(str(args.cases_root.resolve()).encode()).hexdigest()
    try:
        with urlopen(f'http://127.0.0.1:{args.port}/api/replay/health', timeout=1) as response: existing = json.load(response)
    except (OSError, ValueError): existing = None
    if existing is not None:
        micros = int((args.model_budget_rmb * 1000000).to_integral_value(rounding=ROUND_CEILING))
        if existing.get('workspace') != identity or existing.get('model_send_enabled') != args.send_model or (args.send_model and (existing.get('model_budget_ledger') != str(args.model_budget_ledger.resolve()) or existing.get('model_budget_total_micros') != micros)):
            raise SystemExit('端口被其他程序或不同配置占用。请用 --port 8781 更换端口，或先关闭原启动窗口。')
        if not args.no_browser: webbrowser.open(url)
        print('已打开正在运行的见微：' + url, flush=True)
        return
    try: import psutil
    except ImportError: raise SystemExit('依赖未安装。请使用 Windows 运行包，或按 README 安装 requirements.txt。') from None
    prepare_workspace(args.cases_root)
    from run_product_replay import serve
    from event_workbench.product_replay import Workbench
    from event_workbench.news_service import NewsService
    from event_workbench.news_budget import ModelBudget, COMPETITION_PLAN
    from event_workbench.news_joint import VERSION
    from event_workbench.deepseek_credentials import credential_exists
    if args.send_model and not credential_exists():
        raise SystemExit('密钥未配置。可运行 scripts/configure_competition_key.py，或加 --offline 浏览完整保存案例。')
    app = Workbench(args.cases_root)
    args.model_budget_ledger.parent.mkdir(parents=True, exist_ok=True)
    budget = ModelBudget(args.model_budget_ledger, total_rmb=args.model_budget_rmb, plan_id=COMPETITION_PLAN,
        phase='core', batch_id='local-input', run_id='local:training-showcase',
        pricing=json.loads((ROOT / 'data/pricing.json').read_text('utf8')), send_model=True, purpose='business') if args.send_model else None
    service = NewsService(app, args.case_run, send_model=args.send_model, budget=budget, contract_revision=VERSION)
    print('见微：' + url, flush=True)
    print('无需登录。' + ('新新闻分析已启用，点击“导入并分析”或已保存新闻的“分析”后才调用模型。' if args.send_model else '离线模式：可浏览案例和保存材料。'), flush=True)
    print('关闭本窗口或按 Ctrl+C 停止；下次启动保留你的数据。', flush=True)
    if not args.no_browser:
        timer = threading.Timer(1, lambda: webbrowser.open(url)); timer.daemon = True; timer.start()
    serve(app, args.port, service)


if __name__ == '__main__': main()
