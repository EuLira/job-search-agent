"""
Agente de busca de vagas - Herrysson Lira
Busca vagas em APIs gratuitas, calcula score de compatibilidade + estimativa
salarial e escreve os resultados em uma planilha Google Sheets.

Fontes: Arbeitnow, RemoteOK, Adzuna, Jooble (todas com free tier).
"""

import os
import re
import json
import hashlib
import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
with open("resume_profile.json", "r", encoding="utf-8") as f:
    PROFILE = json.load(f)

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY")
JOOBLE_API_KEY = os.environ.get("JOOBLE_API_KEY")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")  # conteúdo do service account JSON
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")  # opcional

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # opcional
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # opcional

SHEET_TAB = "Vagas"
SEEN_TAB = "IDs_Processados"

HEADERS = [
    "Data Coleta", "IO", "Compat. Técnica", "Modalidade Score", "Salário Score",
    "Empresa Score", "Modalidade", "Cargo", "Empresa", "Localização",
    "Salário", "Tipo Salário", "Fonte", "Link", "Palavras-chave Encontradas",
    "Motivo", "Status"
]

MODALITY_RANK = {m: i for i, m in enumerate(PROFILE["modality_priority"])}


# --------------------------------------------------------------------------
# COLETA DE VAGAS
# --------------------------------------------------------------------------
def fetch_arbeitnow():
    jobs = []
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=20)
        r.raise_for_status()
        for j in r.json().get("data", []):
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", "") or "Remoto",
                "description": j.get("description", ""),
                "url": j.get("url", ""),
                "remote": j.get("remote", False),
                "salary_min": None,
                "salary_max": None,
                "source": "Arbeitnow",
            })
    except Exception as e:
        print(f"[Arbeitnow] erro: {e}")
    return jobs


def fetch_remoteok():
    jobs = []
    try:
        r = requests.get("https://remoteok.com/api",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        for j in data[1:]:  # primeiro item é metadata
            jobs.append({
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": j.get("location", "") or "Remoto",
                "description": j.get("description", ""),
                "url": j.get("url", ""),
                "remote": True,
                "salary_min": j.get("salary_min"),
                "salary_max": j.get("salary_max"),
                "source": "RemoteOK",
            })
    except Exception as e:
        print(f"[RemoteOK] erro: {e}")
    return jobs


def fetch_adzuna(pages=2):
    jobs = []
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("[Adzuna] chaves não configuradas, pulando.")
        return jobs
    for title in PROFILE["target_titles"][:3]:  # limita p/ não estourar free tier
        for page in range(1, pages + 1):
            try:
                url = f"https://api.adzuna.com/v1/api/jobs/br/search/{page}"
                params = {
                    "app_id": ADZUNA_APP_ID,
                    "app_key": ADZUNA_APP_KEY,
                    "results_per_page": 20,
                    "what": title,
                    "content-type": "application/json",
                }
                r = requests.get(url, params=params, timeout=20)
                r.raise_for_status()
                for j in r.json().get("results", []):
                    jobs.append({
                        "title": j.get("title", ""),
                        "company": (j.get("company") or {}).get("display_name", ""),
                        "location": (j.get("location") or {}).get("display_name", ""),
                        "description": j.get("description", ""),
                        "url": j.get("redirect_url", ""),
                        "remote": "remot" in (j.get("title", "") + j.get("description", "")).lower(),
                        "salary_min": j.get("salary_min"),
                        "salary_max": j.get("salary_max"),
                        "source": "Adzuna",
                    })
            except Exception as e:
                print(f"[Adzuna] erro ({title}, pg {page}): {e}")
    return jobs


def fetch_jooble():
    jobs = []
    if not JOOBLE_API_KEY:
        print("[Jooble] chave não configurada, pulando.")
        return jobs
    try:
        url = f"https://jooble.org/api/{JOOBLE_API_KEY}"
        body = {"keywords": "Analista de Infraestrutura de TI", "location": "Brasil"}
        r = requests.post(url, json=body, timeout=20)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "description": j.get("snippet", ""),
                "url": j.get("link", ""),
                "remote": "remot" in (j.get("title", "") + j.get("snippet", "")).lower(),
                "salary_min": None,
                "salary_max": None,
                "source": "Jooble",
            })
    except Exception as e:
        print(f"[Jooble] erro: {e}")
    return jobs


# --------------------------------------------------------------------------
# SCORE DE COMPATIBILIDADE
# --------------------------------------------------------------------------
def detect_modality(job):
    text = f"{job['title']} {job['location']} {job['description']}".lower()
    if job.get("remote") or "remoto" in text or "home office" in text or "remote" in text:
        return "remoto"
    if "híbrido" in text or "hibrido" in text or "hybrid" in text:
        return "híbrido"
    return "presencial"


def keyword_matches(job):
    text = f"{job['title']} {job['description']}".lower()
    found_core = [k for k in PROFILE["core_keywords"] if k.lower() in text]
    found_secondary = [k for k in PROFILE["secondary_keywords"] if k.lower() in text]
    return found_core, found_secondary


def title_match_score(job):
    title = job["title"].lower()
    for avoid in PROFILE["seniority_signals"]["avoid_titles"]:
        if avoid in title:
            return 0.0
    best = 0.0
    for target in PROFILE["target_titles"]:
        words_target = set(target.lower().split())
        words_title = set(title.split())
        overlap = len(words_target & words_title) / max(len(words_target), 1)
        best = max(best, overlap)
    return best


GEO_POSITIVE_TERMS = [
    "brasil", "brazil", "latam", "latin america", "américa latina",
    "worldwide", "anywhere", "global", "remote - anywhere", "remote worldwide",
    "fortaleza", "ceará", "ceara", "são paulo", "sao paulo",
]

GEO_NEGATIVE_TERMS = [
    "germany", "alemanha", "berlin", "münchen", "munich", "hamburg", "frankfurt",
    "united kingdom", "uk", " london", "france", "paris", "netherlands", "amsterdam",
    "spain", "españa", "madrid", "italy", "italia", "united states", "usa",
    "us only", "eu only", "european union", "canada", "australia", "portugal",
    "poland", "polska", "austria", "vienna", "switzerland", "zurich",
    "must be based in", "must reside in", "must be located in",
    "eligible to work in the eu", "eligible to work in the uk",
    "authorized to work in the us", "visa sponsorship not available",
]


def detect_geo_eligibility(job):
    """Retorna (fator 0-1, label) indicando se a vaga é compatível com
    candidatos baseados no Brasil, com base na localização e na descrição."""
    text = f"{job.get('location', '')} {job.get('description', '')}".lower()
    if any(p in text for p in GEO_POSITIVE_TERMS):
        return 1.0, "Brasil/Global"
    if any(c in text for c in GEO_NEGATIVE_TERMS):
        return 0.3, "provável exigência de residência fora do BR"
    return 0.75, "localização não especificada"





def get_company_reputation(company_name):
    """Retorna nota 0-1 baseada no Google Places (avaliação pública da empresa).
    Se a API não estiver configurada ou a empresa não for encontrada, retorna
    um valor neutro (0.6) pra não penalizar nem beneficiar indevidamente."""
    if not company_name:
        return 0.6, None
    if company_name in _company_reputation_cache:
        return _company_reputation_cache[company_name]
    if not GOOGLE_PLACES_API_KEY:
        _company_reputation_cache[company_name] = (0.6, None)
        return 0.6, None
    try:
        url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
        params = {
            "input": company_name,
            "inputtype": "textquery",
            "fields": "rating,user_ratings_total",
            "key": GOOGLE_PLACES_API_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        candidates = r.json().get("candidates", [])
        if not candidates or "rating" not in candidates[0]:
            result = (0.6, None)
        else:
            rating = candidates[0]["rating"]  # 0-5
            result = (rating / 5, rating)
        _company_reputation_cache[company_name] = result
        return result
    except Exception as e:
        print(f"[Google Places] erro ({company_name}): {e}")
        _company_reputation_cache[company_name] = (0.6, None)
        return 0.6, None


def benefit_match_score(job):
    text = job.get("description", "").lower()
    found = [b for b in PROFILE["beneficio_keywords"] if b in text]
    ratio = len(found) / max(len(PROFILE["beneficio_keywords"]), 1)
    return min(1.0, ratio * 3), found  # poucos benefícios citados já pontuam bem


def compute_io(job, salary_str, salary_fits):
    """Calcula o Índice de Oportunidade (0-100) com breakdown por critério
    e um motivo textual, seguindo os pesos definidos em resume_profile.json."""
    w = PROFILE["io_weights"]

    found_core, found_secondary = keyword_matches(job)
    core_ratio = len(found_core) / max(len(PROFILE["core_keywords"]), 1)
    secondary_ratio = len(found_secondary) / max(len(PROFILE["secondary_keywords"]), 1)
    title_score = title_match_score(job)
    skill_score = min(1.0, core_ratio * 1.1 + secondary_ratio * 0.4 + title_score * 0.3)

    modality = detect_modality(job)
    modality_base = 1.0 - (MODALITY_RANK.get(modality, 3) / max(len(MODALITY_RANK) - 1, 1))
    geo_factor, geo_label = detect_geo_eligibility(job)
    modality_score = modality_base * geo_factor

    salary_score = 1.0 if salary_fits else 0.4

    company_score, raw_rating = get_company_reputation(job.get("company", ""))

    seniority_score = 1.0 if title_score > 0 else 0.3

    benefit_score, found_benefits = benefit_match_score(job)

    io = (
        skill_score * w["compatibilidade_tecnica"] +
        modality_score * w["modalidade"] +
        salary_score * w["salario"] +
        company_score * w["empresa_reputacao"] +
        seniority_score * w["senioridade"] +
        benefit_score * w["beneficios"]
    )
    io = round(min(100, io), 1)

    # motivo textual, priorizando os fatores mais fortes
    motivos = []
    if skill_score >= 0.7:
        motivos.append("forte aderência técnica")
    elif skill_score < 0.35:
        motivos.append("aderência técnica baixa")
    if modality == "remoto":
        motivos.append("remoto")
    elif modality == "híbrido":
        motivos.append("híbrido")
    else:
        motivos.append("presencial")
    if salary_fits:
        motivos.append("salário dentro da faixa alvo")
    if raw_rating:
        motivos.append(f"empresa avaliada {raw_rating}★")
    if found_benefits:
        motivos.append("benefícios relevantes citados")
    if geo_label == "provável exigência de residência fora do BR":
        motivos.append("⚠️ pode exigir residência fora do Brasil")

    motivo = ", ".join(motivos).capitalize()

    breakdown = {
        "compat_tecnica_pct": round(skill_score * 100, 1),
        "modalidade_pct": round(modality_score * 100, 1),
        "salario_pct": round(salary_score * 100, 1),
        "empresa_pct": round(company_score * 100, 1),
    }

    return io, breakdown, motivo, modality, found_core + found_secondary


# --------------------------------------------------------------------------
# ESTIMATIVA DE SALÁRIO (regra, sem custo de API)
# --------------------------------------------------------------------------
def extract_seniority_level(job):
    text = f"{job['title']} {job['description']}".lower()
    if any(w in text for w in ["especialista", "specialist"]):
        return "especialista"
    if any(w in text for w in ["tech lead", "líder técnico", "team lead"]):
        return "tech_lead"
    if any(w in text for w in ["sênior", "senior", "sr."]):
        return "senior"
    if any(w in text for w in ["pleno", "mid-level", "pl."]):
        return "pleno"
    if any(w in text for w in ["júnior", "junior", "jr."]):
        return "junior"
    return "senior" if PROFILE["seniority_signals"]["level"] == "senior" else "pleno"


def estimate_salary(job):
    # 1. Se a vaga já informa salário, usa o valor real
    if job.get("salary_min") or job.get("salary_max"):
        smin = job.get("salary_min") or job.get("salary_max")
        smax = job.get("salary_max") or job.get("salary_min")
        return f"R$ {smin:,.0f} - R$ {smax:,.0f}".replace(",", "."), "Informado"

    # 2. Tenta achar menção explícita de salário no texto (regex simples)
    text = job.get("description", "")
    m = re.search(r"R\$\s?([\d.,]{4,})", text)
    if m:
        return f"R$ {m.group(1)}", "Informado (texto)"

    # 3. Estimativa por regra: senioridade + presença de skills raras (Fortinet, cloud etc.)
    level = extract_seniority_level(job)
    bench = PROFILE["salary_benchmark_table"].get(level, PROFILE["salary_benchmark_table"]["senior"])
    smin, smax = bench["min"], bench["max"]

    # ajuste: skills de nicho (Fortinet, Cloud, AWS/Azure) puxam a faixa pra cima
    niche = ["fortinet", "aws", "azure", "cloud", "kubernetes"]
    text_low = text.lower()
    if any(n in text_low for n in niche):
        smin = int(smin * 1.1)
        smax = int(smax * 1.15)

    # ajuste: localização fora de capitais grandes puxa levemente pra baixo
    loc = job.get("location", "").lower()
    if loc and not any(c in loc for c in ["são paulo", "rio de janeiro", "remoto", "remote"]):
        smax = int(smax * 0.95)

    return f"R$ {smin:,.0f} - R$ {smax:,.0f}".replace(",", "."), "Estimado"


def salary_fits_target(salary_str):
    nums = [int(n.replace(".", "")) for n in re.findall(r"[\d.]{3,}", salary_str)]
    if not nums:
        return False
    smin, smax = min(nums), max(nums)
    target = PROFILE["salary_target"]
    return smax >= target["min"] and smin <= target["max"]


# --------------------------------------------------------------------------
# NOTIFICAÇÃO (TELEGRAM)
# --------------------------------------------------------------------------
def notify_telegram(job, io_score, motivo, salary_str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    text = (
        f"🔥 Vaga com IO {io_score} encontrada!\n\n"
        f"*{job['title']}* — {job['company']}\n"
        f"📍 {job['location']}\n"
        f"💰 {salary_str}\n"
        f"✅ {motivo}\n"
        f"🔗 {job['url']}"
    )
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }, timeout=10)
    except Exception as e:
        print(f"[Telegram] erro ao notificar: {e}")


# --------------------------------------------------------------------------
# GOOGLE SHEETS
# --------------------------------------------------------------------------
def get_sheet_client():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_tab(spreadsheet, tab_name, headers):
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=2000, cols=len(headers) + 2)
        ws.append_row(headers)
    return ws


def job_id(job):
    raw = f"{job['title']}|{job['company']}|{job['url']}"
    return hashlib.md5(raw.encode()).hexdigest()


def main():
    print("Coletando vagas...")
    all_jobs = (
        fetch_arbeitnow() +
        fetch_remoteok() +
        fetch_adzuna() +
        fetch_jooble()
    )
    print(f"Total bruto coletado: {len(all_jobs)}")

    client = get_sheet_client()
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    ws = get_or_create_tab(spreadsheet, SHEET_TAB, HEADERS)
    seen_ws = get_or_create_tab(spreadsheet, SEEN_TAB, ["job_id"])

    seen_ids = set(v[0] for v in seen_ws.get_all_values()[1:] if v)

    rows_to_add = []
    new_seen_ids = []

    for job in all_jobs:
        if not job.get("title") or not job.get("url"):
            continue
        jid = job_id(job)
        if jid in seen_ids:
            continue

        salary_str, salary_type = estimate_salary(job)
        fits = salary_fits_target(salary_str)

        io_score, breakdown, motivo, modality, matched_keywords = compute_io(job, salary_str, fits)

        if io_score < PROFILE.get("minimum_score", 40):  # descarta baixíssima aderência
            new_seen_ids.append(jid)
            continue

        rows_to_add.append({
            "row": [
                datetime.date.today().isoformat(),
                io_score,
                breakdown["compat_tecnica_pct"],
                breakdown["modalidade_pct"],
                breakdown["salario_pct"],
                breakdown["empresa_pct"],
                modality,
                job["title"],
                job["company"],
                job["location"],
                salary_str,
                salary_type,
                job["source"],
                job["url"],
                ", ".join(matched_keywords[:8]),
                motivo,
                "Nova",
            ],
            "modality_rank": MODALITY_RANK.get(modality, 3),
            "score": io_score,
        })
        new_seen_ids.append(jid)

        if io_score >= PROFILE.get("io_alert_threshold", 90):
            notify_telegram(job, io_score, motivo, salary_str)

    # ordena: modalidade (remoto primeiro) e depois score desc
    rows_to_add.sort(key=lambda r: (r["modality_rank"], -r["score"]))

    if rows_to_add:
        ws.append_rows([r["row"] for r in rows_to_add], value_input_option="USER_ENTERED")
        print(f"{len(rows_to_add)} vagas novas adicionadas à planilha.")
    else:
        print("Nenhuma vaga nova relevante encontrada nesta execução.")

    if new_seen_ids:
        seen_ws.append_rows([[i] for i in new_seen_ids])


if __name__ == "__main__":
    main()
