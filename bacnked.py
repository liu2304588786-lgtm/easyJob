import sys
import requests
from bs4 import BeautifulSoup
import re
import datetime
import time
import traceback
import uuid  # 新增: 用于生成唯一ID
from flask import Flask, jsonify, request
from flask_cors import CORS

# 强制 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置区域 =================
CHANNEL_USERNAME = 'DeJob_official'
PROXY = None 

# ================= 智能解析逻辑 =================
class JobParser:
    @staticmethod
    def clean_string(text):
        """深度清洗字符串"""
        if not text: return ""
        # 1. 去除 #及其后面的内容
        text = re.sub(r'[#＃].*', '', text)
        # 2. 去除 Emoji
        text = re.sub(r'[^\w\s\u4e00-\u9fa5:：\.\-\(\)\+]', '', text)
        # 3. 去除常见前缀
        text = re.sub(r'^[【\[]?(?:招聘|岗位|职位|Job|Hiring|Position)[\]】]?[:：]?\s*', '', text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def extract_max_salary(raw_text):
        """提取薪资最大值"""
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

            # --- 核心过滤逻辑：必须包含 #招聘 标签 ---
            if "#招聘" not in raw_text and "＃招聘" not in raw_text:
                return None

            time_span = msg_div.find('a', class_='tgme_widget_message_date')
            date_str = datetime.date.today().strftime("%Y-%m-%d")
            if time_span:
                time_tag = time_span.find('time')
                if time_tag and time_tag.has_attr('datetime'):
                    date_str = time_tag['datetime'].split('T')[0]

            # 提取标签
            hashtags = re.findall(r'[#＃]([\w\-\.\+\u4e00-\u9fa5]+)', raw_text)

            # 生成绝对唯一 ID (防止 ID 冲突导致选不中)
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
            
            # 关键词白名单
            job_keywords = [
                "工程师", "运营", "市场", "实习生", "BD", "专员", "经理", "设计师", "交易员", "负责人"
            ]

            # 标签黑名单
            blacklist_tags = [
                "research", "socialfi", "defi", "web3", "crypto", "blockchain", "gamefi", "nft", "dao",
                "headhunter", "recruiter", "hiring", "job", "fulltime", "parttime", "remote", "apply",
                "work", "career", "talent", "exchange", "wallet", "public", "chain", "infrastructure"
            ]

            # 1. 找公司
            for line in lines:
                if not found_company:
                    company_match = re.match(r'^(?:项目|Project|公司|Company|Team)\s*[:：]\s*(.+)', line, re.IGNORECASE)
                    if company_match:
                        found_company = JobParser.clean_string(company_match.group(1))
                        break
            
            if not found_company:
                for line in lines:
                    if not line.startswith('#') and not line.startswith('＃') and len(line) < 40 and "招聘" not in line:
                        found_company = JobParser.clean_string(line)
                        break

            # 2. 找岗位 (Tag Only)
            target_tag = None
            for tag in hashtags:
                tag_lower = tag.lower()
                is_blacklisted = False
                for bad_word in blacklist_tags:
                    if bad_word in tag_lower:
                        is_blacklisted = True
                        break
                if is_blacklisted:
                    continue
                
                for keyword in job_keywords:
                    if keyword.lower() in tag_lower:
                        target_tag = tag
                        break
                if target_tag:
                    break
            
            if target_tag:
                found_title = target_tag
            else:
                found_title = "其他"

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

        except Exception as e:
            return None

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
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

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
                        if job['email'] or len(job['raw_content']) > 20:
                            page_jobs.append(job)
                
                all_jobs.extend(page_jobs)
                print(f"    -> 解析到 {len(page_jobs)} 个职位")

                link = soup.find('a', class_='tme_messages_more')
                if link and link.get('href'):
                    href = link['href']
                    target_url = href if href.startswith('http') else f"https://t.me{href}"
                    time.sleep(1.5)
                else:
                    break
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
    if not scraper.cached_jobs:
        scraper.fetch_jobs(60)
    return jsonify(scraper.cached_jobs)

@app.route('/api/send-resume', methods=['POST'])
def send():
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    print("Server running on http://localhost:5000")
    app.run(port=5000, debug=True, use_reloader=False)