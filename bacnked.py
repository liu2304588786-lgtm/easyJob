import sys
import requests
from bs4 import BeautifulSoup
import re
import datetime
import time
import traceback
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
        """
        深度清洗字符串：
        1. 去除 #标签 (如 '#CEX')
        2. 去除 Emoji
        3. 去除多余空格
        """
        if not text: return ""
        
        # 去除 #后面的所有内容 (包括 #本身)
        # 例如: "OneBullEx #CEX" -> "OneBullEx"
        text = re.sub(r'#.*', '', text)
        
        # 去除 Emoji (简单范围)
        text = re.sub(r'[^\w\s\u4e00-\u9fa5:：\.\-\(\)]', '', text)
        
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

            time_span = msg_div.find('a', class_='tgme_widget_message_date')
            date_str = datetime.date.today().strftime("%Y-%m-%d")
            if time_span:
                time_tag = time_span.find('time')
                if time_tag and time_tag.has_attr('datetime'):
                    date_str = time_tag['datetime'].split('T')[0]

            job_data = {
                "id": msg_div.get('data-post-id', str(int(time.time()))),
                "date": date_str,
                "raw_content": raw_text,
                "tags": [],
                "type": "全职",
                "location": "远程",
                "email": "",
                "company": "",
                "title": ""
            }

            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            
            found_company = None
            found_title = None
            
            for line in lines:
                # 1. 识别公司/项目
                if not found_company:
                    company_match = re.match(r'^(?:项目|Project|公司|Company|Team)\s*[:：]\s*(.+)', line, re.IGNORECASE)
                    if company_match:
                        # 核心修改：立即清洗公司名
                        found_company = JobParser.clean_string(company_match.group(1))
                        continue 

                # 2. 识别岗位
                if not found_title:
                    job_match = re.match(r'^(?:岗位|职位|Job|Role|Position)\s*[:：]\s*(.+)', line, re.IGNORECASE)
                    if job_match:
                        found_title = JobParser.clean_string(job_match.group(1))
                        continue

            # 3. 智能兜底 (第一行通常是公司名)
            if not found_company and not found_title:
                for line in lines:
                    if not line.startswith('#') and len(line) < 40 and "招聘" not in line:
                        found_company = JobParser.clean_string(line)
                        break
            
            # 4. 如果有公司没岗位，尝试找剩下的第一行文本
            if found_company and not found_title:
                for line in lines:
                    cleaned_line = JobParser.clean_string(line)
                    # 确保不是公司名本身，且长度合适
                    if cleaned_line != found_company and not line.startswith('#') and len(line) < 50 and "招聘" not in line:
                        if not re.search(r'\d+k', line.lower()) and '@' not in line:
                            found_title = cleaned_line
                            break
            
            job_data["company"] = found_company if found_company else "其他项目"
            job_data["title"] = found_title if found_title else "查看详情"

            job_data["salary"] = JobParser.extract_max_salary(raw_text)
            
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.[a-zA-Z]+', raw_text)
            job_data["email"] = email_match.group(0) if email_match else ""

            hashtags = re.findall(r'#(\w+)', raw_text)
            job_data["tags"] = hashtags
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