import sys
import requests
from bs4 import BeautifulSoup
import re
import datetime
import time
import traceback
import uuid
import json
import smtplib
import sqlite3
import threading
import atexit
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from flask import Flask, jsonify, request, send_file, redirect, session, url_for
from flask_cors import CORS
import os
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import base64
from email.mime.base import MIMEBase
from email import encoders

sys.stdout.reconfigure(encoding='utf-8')

CHANNEL_USERNAME = 'DeJob_official'
PROXY = None

# ================= OAuth2 配置 =================
# Gmail OAuth2 配置 - 用户需要从Google Cloud Console获取
# 创建项目：https://console.cloud.google.com/
# 启用Gmail API，创建OAuth2客户端凭证（桌面应用类型）
# 将客户端ID和密钥保存在本地配置文件中
OAUTH_CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get("GMAIL_CLIENT_ID", ""),
        "client_secret": os.environ.get("GMAIL_CLIENT_SECRET", ""),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:5000/oauth2callback"]
    }
}

# 本地存储令牌的文件
TOKEN_FILE = 'gmail_token.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# ================= 数据库配置 =================
DB_PATH = 'jobs.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            salary TEXT,
            date TEXT,
            email TEXT,
            location TEXT,
            raw_content TEXT,
            tags TEXT,
            type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_date ON jobs(date DESC)
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_company ON jobs(company)
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_type ON jobs(type)
    ''')
    conn.commit()
    conn.close()

def save_jobs_to_db(jobs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for job in jobs:
        # 将tags列表转换为JSON字符串
        tags_json = json.dumps(job.get('tags', []), ensure_ascii=False)
        c.execute('''
            INSERT OR REPLACE INTO jobs
            (id, company, title, salary, date, email, location, raw_content, tags, type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            job['id'],
            job.get('company', ''),
            job.get('title', ''),
            job.get('salary', ''),
            job.get('date', ''),
            job.get('email', ''),
            job.get('location', ''),
            job.get('raw_content', ''),
            tags_json,
            job.get('type', '')
        ))
    conn.commit()
    conn.close()

def load_jobs_from_db(days=60):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    c.execute('''
        SELECT id, company, title, salary, date, email, location, raw_content, tags, type
        FROM jobs
        WHERE date >= ?
        ORDER BY date DESC
    ''', (cutoff_date,))

    jobs = []
    for row in c.fetchall():
        job = {
            'id': row[0],
            'company': row[1],
            'title': row[2],
            'salary': row[3],
            'date': row[4],
            'email': row[5],
            'location': row[6],
            'raw_content': row[7],
            'tags': json.loads(row[8]) if row[8] else [],
            'type': row[9]
        }
        jobs.append(job)
    conn.close()
    return jobs

def cleanup_old_jobs(days=90):
    """清理90天前的旧数据"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    c.execute('DELETE FROM jobs WHERE date < ?', (cutoff_date,))
    conn.commit()
    conn.close()

# 初始化数据库
init_db() 

# ================= 智能解析逻辑 (保持不变) =================
class JobParser:
    @staticmethod
    def clean_string(text):
        if not text: return ""
        text = re.sub(r'[#＃].*', '', text)
        text = re.sub(r'[^\w\s\u4e00-\u9fa5:：\.\-\(\)\+]', '', text)
        text = re.sub(r'^[【\[]?(?:招聘|岗位|职位|Job|Hiring|Position)[\]】]?[:：]?\s*', '', text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def extract_max_salary(raw_text):
        lines = raw_text.split('\n')
        target_line = ""
        for line in lines:
            if any(k in line for k in ["薪资", "待遇", "Salary", "Pay", "U", "$"]):
                if len(line) < 50 and re.search(r'\d', line):
                    target_line = line
                    break
        if not target_line: return "面议"
        numbers = re.findall(r'(\d+)', target_line)
        if not numbers: return target_line
        nums = [int(n) for n in numbers]
        valid_nums = [n for n in nums if (n > 100 and n < 100000) or (n < 100 and 'k' in target_line.lower())]
        if not valid_nums: return target_line
        max_val = max(valid_nums)
        unit = ""
        lower_line = target_line.lower()
        if "u" in lower_line: unit = "U"
        elif "$" in target_line: unit = "$"
        elif "k" in lower_line: unit = "k"
        if unit == "k" and max_val < 1000: return f"{max_val}k"
        return f"{max_val}{unit}"

    @staticmethod
    def parse_html_message(msg_div):
        try:
            text_div = msg_div.find('div', class_='tgme_widget_message_text')
            if not text_div: return None
            for br in text_div.find_all("br"): br.replace_with("\n")
            raw_text = text_div.get_text()

            if "#招聘" not in raw_text and "＃招聘" not in raw_text:
                return None

            time_span = msg_div.find('a', class_='tgme_widget_message_date')
            date_str = datetime.date.today().strftime("%Y-%m-%d")
            if time_span:
                time_tag = time_span.find('time')
                if time_tag and time_tag.has_attr('datetime'):
                    date_str = time_tag['datetime'].split('T')[0]

            hashtags = re.findall(r'[#＃]([\w\-\.\+\u4e00-\u9fa5]+)', raw_text)
            unique_id = msg_div.get('data-post-id')
            if not unique_id:
                unique_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

            job_data = {
                "id": unique_id,
                "date": date_str,
                "raw_content": raw_text,
                "tags": hashtags,
                "type": "全职",
                "location": "远程",
                "email": "",
                "company": "",
                "title": ""
            }

            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            found_company = None
            found_title = None
            
            job_keywords = ["工程师", "运营", "市场", "实习生", "BD", "专员", "经理", "设计师", "交易员", "负责人"]
            blacklist_tags = ["research", "socialfi", "defi", "web3", "crypto", "blockchain", "gamefi", "nft", "dao", "headhunter", "recruiter", "hiring", "job", "fulltime", "parttime", "remote", "apply", "work", "career", "talent", "exchange", "wallet", "public", "chain", "infrastructure"]
            invalid_company_names = ["简介", "介绍", "岗位", "职责", "要求", "福利", "待遇", "关于我们", "About", "Intro", "Description", "Requirements", "Welcome"]

            for line in lines:
                if not found_company:
                    company_match = re.match(r'^(?:项目|Project|公司|Company|Team)\s*[:：]\s*(.+)', line, re.IGNORECASE)
                    if company_match:
                        found_company = JobParser.clean_string(company_match.group(1))
                        break
            if not found_company:
                for line in lines:
                    cleaned_line = JobParser.clean_string(line)
                    if not line.startswith('#') and not line.startswith('＃') and len(line) < 40 and "招聘" not in line:
                        if cleaned_line in invalid_company_names or len(cleaned_line) < 2: continue
                        found_company = cleaned_line
                        break

            target_tag = None
            for tag in hashtags:
                tag_lower = tag.lower()
                if any(bad in tag_lower for bad in blacklist_tags): continue
                for keyword in job_keywords:
                    if keyword.lower() in tag_lower:
                        target_tag = tag
                        break
                if target_tag: break
            
            found_title = target_tag if target_tag else "其他"

            job_data["company"] = found_company if found_company else "其他项目"
            job_data["title"] = found_title
            job_data["salary"] = JobParser.extract_max_salary(raw_text)
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.[a-zA-Z]+', raw_text)
            job_data["email"] = email_match.group(0) if email_match else ""

            for tag in hashtags:
                if "兼职" in tag: job_data["type"] = "兼职"
                if "实习" in tag: job_data["type"] = "实习"
                if "外包" in tag or "项目" in tag: job_data["type"] = "项目制"

            return job_data
        except Exception as e: return None

# ================= 爬虫服务 =================
class WebScraper:
    def __init__(self):
        self.base_url = f"https://t.me/s/{CHANNEL_USERNAME}"
        self.cached_jobs = []

    def fetch_jobs(self, lookback_days=60):
        print(f"[*] 启动 Web 抓取: {self.base_url}")
        all_jobs = []
        target_url = self.base_url
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=lookback_days)
        headers = {"User-Agent": "Mozilla/5.0"}

        for page in range(5):
            try:
                print(f"[*] 请求第 {page+1} 页...")
                resp = requests.get(target_url, headers=headers, proxies=PROXY, timeout=15)
                if resp.status_code != 200: break
                soup = BeautifulSoup(resp.text, 'html.parser')
                divs = soup.find_all('div', class_='tgme_widget_message_wrap')
                if not divs: break
                page_jobs = []
                for div in reversed(divs):
                    job = JobParser.parse_html_message(div)
                    if job:
                        if job['date']:
                            try:
                                d = datetime.datetime.strptime(job['date'], "%Y-%m-%d")
                                if d < cutoff_date: 
                                    self.cached_jobs = all_jobs + page_jobs
                                    return self.cached_jobs
                            except: pass
                        if job['email'] or len(job['raw_content']) > 20: page_jobs.append(job)
                all_jobs.extend(page_jobs)
                print(f"    -> 解析到 {len(page_jobs)} 个职位")
                link = soup.find('a', class_='tme_messages_more')
                if link and link.get('href'):
                    href = link['href']
                    target_url = href if href.startswith('http') else f"https://t.me{href}"
                    time.sleep(1.5)
                else: break
            except Exception as e:
                print(f"[!] 错误: {e}")
                break
        self.cached_jobs = all_jobs
        print(f"[*] 抓取结束，共 {len(all_jobs)} 条")

        # 保存到数据库
        if all_jobs:
            try:
                save_jobs_to_db(all_jobs)
                print(f"[*] 已保存 {len(all_jobs)} 条数据到数据库")
                # 清理旧数据
                cleanup_old_jobs(90)
            except Exception as e:
                print(f"[!] 数据库保存失败: {e}")

        return all_jobs

# ================= Flask App =================
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")
CORS(app)
scraper = WebScraper()

# ================= OAuth2 辅助函数 =================
def get_gmail_credentials():
    """获取或刷新Gmail API凭据"""
    creds = None
    # 从文件加载令牌
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"[!] 加载令牌失败: {e}")

    # 如果令牌无效或过期，尝试刷新
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("[*] 令牌已刷新")
            except Exception as e:
                print(f"[!] 刷新令牌失败: {e}")
                creds = None
        else:
            print("[*] 无有效令牌，需要重新授权")

    return creds

def save_gmail_credentials(creds):
    """保存Gmail API凭据到文件"""
    with open(TOKEN_FILE, 'w') as token_file:
        token_file.write(creds.to_json())
    print(f"[*] 令牌已保存到 {TOKEN_FILE}")

# ================= 后台定时更新 =================
def background_update():
    """后台定时更新数据"""
    while True:
        try:
            print(f"[*] [{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始定时更新...")
            jobs = scraper.fetch_jobs(60)
            print(f"[*] 定时更新完成，获取 {len(jobs)} 条数据")
        except Exception as e:
            print(f"[!] 定时更新失败: {e}")
        # 每2小时更新一次
        time.sleep(2 * 60 * 60)

# 启动后台更新线程
update_thread = threading.Thread(target=background_update, daemon=True)
update_thread.start()
print("[*] 后台定时更新线程已启动 (每2小时更新一次)")

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    try:
        # 首先尝试从数据库加载
        db_jobs = load_jobs_from_db(60)  # 加载60天内的数据
        if db_jobs:
            scraper.cached_jobs = db_jobs
            print(f"[*] 从数据库加载 {len(db_jobs)} 条数据")
            return jsonify(db_jobs)
    except Exception as e:
        print(f"[!] 数据库读取失败: {e}")

    # 数据库无数据或读取失败，则爬取
    print("[*] 数据库无数据，开始爬取...")
    if not scraper.cached_jobs:
        scraper.fetch_jobs(60)
    return jsonify(scraper.cached_jobs)

@app.route('/api/send-resume', methods=['POST'])
def send_resume_real():
    # 1. 验证文件
    if 'resume' not in request.files: return jsonify({"status": "error", "msg": "未找到简历"}), 400
    file = request.files['resume']
    
    # 2. 验证配置 (从前端动态获取)
    smtp_user = request.form.get('smtp_user')
    smtp_pass = request.form.get('smtp_pass')
    smtp_host = request.form.get('smtp_host', 'smtp.qq.com') # 默认QQ
    smtp_port = int(request.form.get('smtp_port', 587))

    if not smtp_user or not smtp_pass:
        return jsonify({"status": "error", "msg": "请填写邮箱配置"}), 400

    try:
        job_ids = json.loads(request.form.get('jobIds'))
    except: return jsonify({"status": "error", "msg": "职位ID解析失败"}), 400

    file_content = file.read()
    file_name = file.filename
    success_count = 0
    failed_jobs = []

    print(f"[*] 用户 {smtp_user} 正在投递 {len(job_ids)} 个职位...")

    try:
        # 动态连接 SMTP
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)

        for jid in job_ids:
            target_job = next((j for j in scraper.cached_jobs if str(j['id']) == str(jid)), None)
            if not target_job or not target_job['email']: continue

            target_email = target_job['email']
            job_title = target_job.get('title', 'Position')
            
            msg = MIMEMultipart()
            msg['From'] = smtp_user
            msg['To'] = target_email
            msg['Subject'] = f"应聘：{job_title} - {file_name.replace('.pdf', '')}"

            body = f"""您好，\n\n我对贵公司发布的 [{job_title}] 职位非常感兴趣。\n附件是我的个人简历，请查收。\n\n期待您的回复。"""
            msg.attach(MIMEText(body, 'plain'))

            part = MIMEApplication(file_content, Name=file_name)
            part['Content-Disposition'] = f'attachment; filename="{file_name}"'
            msg.attach(part)

            try:
                server.send_message(msg)
                success_count += 1
                time.sleep(2)
            except Exception as e:
                failed_jobs.append(target_email)

        server.quit()
        return jsonify({"status": "success", "sent": success_count, "failed": len(failed_jobs)})

    except Exception as e:
        return jsonify({"status": "error", "msg": f"邮箱登录失败: {str(e)}，请检查授权码"}), 500

@app.route('/api/oauth2/auth')
def oauth2_auth():
    """开始Gmail OAuth2授权流程"""
    # 检查是否配置了客户端ID和密钥
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")

    if not client_id or not client_secret:
        return jsonify({
            "status": "error",
            "msg": "Gmail OAuth2未配置。请设置环境变量 GMAIL_CLIENT_ID 和 GMAIL_CLIENT_SECRET。"
        }), 400

    try:
        # 创建OAuth2流程
        flow = Flow.from_client_config(
            OAUTH_CLIENT_CONFIG,
            scopes=SCOPES,
            redirect_uri=OAUTH_CLIENT_CONFIG['web']['redirect_uris'][0]
        )

        # 生成授权URL
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )

        # 保存state到session
        session['oauth2_state'] = state

        return jsonify({
            "status": "success",
            "auth_url": authorization_url
        })

    except Exception as e:
        print(f"[!] OAuth2授权URL生成失败: {e}")
        return jsonify({
            "status": "error",
            "msg": f"OAuth2配置错误: {str(e)}"
        }), 500

@app.route('/oauth2callback')
def oauth2_callback():
    """OAuth2回调处理"""
    # 验证state
    state = session.get('oauth2_state')
    if not state or state != request.args.get('state'):
        return "State验证失败，请重试。", 400

    try:
        # 创建流程
        flow = Flow.from_client_config(
            OAUTH_CLIENT_CONFIG,
            scopes=SCOPES,
            redirect_uri=OAUTH_CLIENT_CONFIG['web']['redirect_uris'][0],
            state=state
        )

        # 交换授权码为令牌
        flow.fetch_token(authorization_response=request.url)

        # 获取凭据
        credentials = flow.credentials

        # 保存凭据
        save_gmail_credentials(credentials)

        return redirect('/?oauth2_success=true')

    except Exception as e:
        print(f"[!] OAuth2回调处理失败: {e}")
        return f"授权失败: {str(e)}", 400

@app.route('/api/send-resume-gmail', methods=['POST'])
def send_resume_gmail():
    """使用Gmail API发送简历"""
    # 1. 验证文件
    if 'resume' not in request.files:
        return jsonify({"status": "error", "msg": "未找到简历"}), 400

    file = request.files['resume']

    # 2. 获取凭据
    creds = get_gmail_credentials()
    if not creds:
        return jsonify({
            "status": "error",
            "msg": "Gmail未授权。请先完成OAuth2授权流程。"
        }), 401

    try:
        job_ids = json.loads(request.form.get('jobIds'))
    except:
        return jsonify({"status": "error", "msg": "职位ID解析失败"}), 400

    file_content = file.read()
    file_name = file.filename
    success_count = 0
    failed_jobs = []

    print(f"[*] 使用Gmail API投递 {len(job_ids)} 个职位...")

    try:
        # 创建Gmail服务
        service = build('gmail', 'v1', credentials=creds)

        for jid in job_ids:
            target_job = next((j for j in scraper.cached_jobs if str(j['id']) == str(jid)), None)
            if not target_job or not target_job['email']:
                continue

            target_email = target_job['email']
            job_title = target_job.get('title', 'Position')

            # 创建邮件
            message = MIMEMultipart()
            message['to'] = target_email
            message['from'] = creds.id_token['email'] if hasattr(creds, 'id_token') and creds.id_token else 'me'
            message['subject'] = f"应聘：{job_title} - {file_name.replace('.pdf', '')}"

            # 添加正文
            body = f"""您好，\n\n我对贵公司发布的 [{job_title}] 职位非常感兴趣。\n附件是我的个人简历，请查收。\n\n期待您的回复。"""
            message.attach(MIMEText(body, 'plain'))

            # 添加附件
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(file_content)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{file_name}"')
            message.attach(part)

            # 编码邮件
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

            try:
                # 发送邮件
                service.users().messages().send(
                    userId='me',
                    body={'raw': raw_message}
                ).execute()

                success_count += 1
                time.sleep(2)  # 避免速率限制

            except Exception as e:
                print(f"[!] 发送失败到 {target_email}: {e}")
                failed_jobs.append(target_email)

        return jsonify({
            "status": "success",
            "sent": success_count,
            "failed": len(failed_jobs)
        })

    except Exception as e:
        print(f"[!] Gmail API错误: {e}")
        return jsonify({
            "status": "error",
            "msg": f"Gmail发送失败: {str(e)}"
        }), 500

@app.route('/api/oauth2/status')
def oauth2_status():
    """检查OAuth2授权状态"""
    creds = get_gmail_credentials()
    if creds and creds.valid:
        return jsonify({
            "status": "authorized",
            "email": creds.id_token.get('email') if hasattr(creds, 'id_token') and creds.id_token else "未知"
        })
    else:
        return jsonify({
            "status": "unauthorized",
            "msg": "未授权或令牌已过期"
        })

@app.route('/')
def serve_index():
    return send_file('index.html')

if __name__ == '__main__':
    print("Server running on http://localhost:5000")
    app.run(port=5000, debug=True, use_reloader=False)