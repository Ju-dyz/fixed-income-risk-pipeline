from pathlib import Path
import os, yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

def settings():
    load_dotenv(ROOT / '.env')
    with open(ROOT / 'config/pipeline.yml', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    cfg['root'] = ROOT
    return cfg

def mysql_url(include_db=True):
    db = os.getenv('MYSQL_DATABASE', 'fixed_income_risk_pipeline')
    suffix = f'/{db}' if include_db else '/'
    from urllib.parse import quote_plus
    return (f"mysql+pymysql://{quote_plus(os.environ['MYSQL_USER'])}:"
            f"{quote_plus(os.environ['MYSQL_PASSWORD'])}@{os.getenv('MYSQL_HOST','127.0.0.1')}:"
            f"{os.getenv('MYSQL_PORT','3306')}{suffix}?charset=utf8mb4")

