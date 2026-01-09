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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from flask import Flask, jsonify, request
from flask_cors import CORS

sys.stdout.reconfigure(encoding='utf-8')

CHANNEL_USERNAME = 'DeJob_official'
PROXY = None 

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
        return all_jobs

# ================= Flask App =================
app = Flask(__name__)
CORS(app)
scraper = WebScraper()

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    if not scraper.cached_jobs: scraper.fetch_jobs(60)
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

if __name__ == '__main__':
    print("Server running on http://localhost:5000")
    app.run(port=5000, debug=True, use_reloader=False)