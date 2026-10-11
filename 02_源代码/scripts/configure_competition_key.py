"""Replace the portable configuration interactively; no network requests."""
import getpass,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from event_workbench.deepseek_credentials import save_credential,credential_exists,CredentialError
if __name__=='__main__':
    try:
        if credential_exists() and input('本机已有密钥，输入 REPLACE 才覆盖：')!='REPLACE':raise SystemExit('已取消。')
        secret=getpass.getpass('输入 DeepSeek API 密钥（不显示、不联网）：')
        save_credential(secret,overwrite=True);secret=None
        print('密钥已保存到 config/deepseek.json。重新启动见微后生效。')
    except CredentialError as error:raise SystemExit('保存失败：'+error.code)
