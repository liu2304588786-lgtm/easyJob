import requests
from bs4 import BeautifulSoup
import re
import datetime
import time
import traceback
from flask import Flask, jsonify, request
from flask_cors import CORS

# ================= 配置区域 =================
CHANNEL_USERNAME = 'DeJob_official'

# 代理配置 (Let'sVPN / Tun模式 VPN 请设为 None)
PROXY = None 
# PROXY = {"https": "http://127.0.0.1:7890"} # Clash 用户备用

# ================= 智能解析逻辑 =================
class JobParser:
    @staticmethod
    def parse_salary(text):
        """
        解析薪资逻辑：
        1. 寻找数字范围 (e.g., 1400-1800, 20k-30k)
        2. 取最大值
        """
        # 尝试匹配范围，例如 "1400-1800", "15k-25k", "1000U - 2000U"
        # 逻辑：找两个数字，中间可能有 - 或 to
        try:
            # 预处理：将 'k' 替换为 '000' 以便比较，但在显示时我们可能希望保留原样？
            # 策略：提取所有看起来像薪资的数字块
            
            # 匹配模式：数字 + 可选的k/w + 可选的货币符号
            # 这是一个简化的启发式处理
            matches = re.findall(r'(\d+)(k|K|w|W)?', text)
            if not matches:
                return "面议"

            # 过滤掉像年份 "2023" 这样的误判 (通常薪资不会刚好是 2023, 2024)
            # 但简单起见，我们假设正则是在 "薪资：" 这一行里跑的
            
            # 如果是范围，通常出现在同一行。
            # 我们针对包含 "薪资"、"待遇"、"Salary" 的行进行特异性分析
            pass
        except:
            return "面议"
        return "面议"

    @staticmethod
    def extract_max_salary(raw_text):
        """
        从文本中提取薪资行，并计算最大值
        """
        lines = raw_text.split('\n')
        target_line = ""
        for line in lines:
            if any(k in line for k in ["薪资", "待遇", "Salary", "Pay", "U", "$"]):
                # 排除仅仅是标签的行
                if len(line) > 50: continue # 太长的可能不是薪资行
                if re.search(r'\d', line):
                    target_line = line
                    break
        
        if not target_line:
            return "面议"

        # 提取所有数字
        # 处理 1.5k 这种情况比较复杂，这里做简化处理：提取纯整数
        # 处理 1500-2000U
        numbers = re.findall(r'(\d+)', target_line)
        if not numbers:
            return target_line # 如果没数字，直接返回文本（如“面议”）

        # 转换为整数列表
        nums = [int(n) for n in numbers]
        
        # 简单过滤：忽略过小的数字（可能是 1-3年经验的 1 或 3）
        # 假设薪资通常 > 100
        valid_nums = [n for n in nums if n > 100 or (n < 100 and 'k' in target_line.lower())]
        
        if not valid_nums:
            return target_line

        # 寻找最大值
        max_val = max(valid_nums)
        
        # 尝试恢复单位
        unit = ""
        if "u" in target_line.lower(): unit = "U"
        elif "$" in target_line: unit = "$"
        elif "k" in target_line.lower(): 
            unit = "k"
            # 如果提取的是 20 (k), 这里的 max_val 是 20
        
        # 如果原文是 20k，我们提取了 20，需要拼回去
        if unit == "k" and max_val < 1000:
             return f"{max_val}k"
        
        return f"{max_val}{unit}"

    @staticmethod
    def parse_html_message(msg_div):
        try:
            # --- 1. 获取文本 ---
            text_div = msg_div.find('div', class_='tgme_widget_message_text')
            if not text_div: return None
            
            for br in text_div.find_all("br"): br.replace_with("\n")
            raw_text = text_div.get_text()

            # --- 2. 获取时间 ---
            time_span = msg_div.find('a', class_='tgme_widget_message_date')
            date_str = datetime.date.today().strftime("%Y-%m-%d")
            if time_span:
                time_tag = time_span.find('time')
                if time_tag and time_tag.has_attr('datetime'):
                    date_str = time_tag['datetime'].split('T')[0]

            # --- 3. 智能字段提取 ---
            job_data = {
                "id": msg_div.get('data-post-id', str(int(time.time()))),
                "date": date_str,
                "raw_content": raw_text, # 保存全文供前端展示详情
                "tags": [],
                "type": "全职", # 默认
                "location": "远程", # 默认
            }

            # A. 提取标题 (Priority: 项目 > 公司 > 岗位 > 第一行)
            # 用户希望 "Marisa" (项目/公司名) 作为标题
            lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
            title_candidates = []
            
            for line in lines:
                # 移除 emoji 以便匹配
                clean_line = re.sub(r'[^\w\s\u4e00-\u9fa5:：]', '', line) 
                
                # 优先级 1: 项目名称 / 公司名称
                if any(k in clean_line for k in ["项目", "Project", "公司", "Company"]):
                    # 提取冒号后的内容
                    parts = re.split(r'[:：]', line, 1)
                    if len(parts) > 1 and len(parts[1].strip()) > 1:
                        job_data["title"] = parts[1].strip() # 找到即停止
                        break
                
                # 优先级 2: 岗位名称 (如果没找到项目名，暂存这个)
                if any(k in clean_line for k in ["岗位", "职位", "Job", "Role", "Position"]):
                    parts = re.split(r'[:：]', line, 1)
                    if len(parts) > 1:
                        title_candidates.append(parts[1].strip())

            # 如果没找到 "项目/公司"，但找到了 "岗位"，用岗位
            if "title" not in job_data:
                if title_candidates:
                    job_data["title"] = title_candidates[0]
                else:
                    # 兜底：找第一行非标签的内容
                    for line in lines:
                        if not line.startswith('#') and len(line) < 50:
                            job_data["title"] = line
                            break
                    else:
                        job_data["title"] = "招聘详情"

            # B. 提取薪资 (取最大值)
            # 简单正则逻辑：寻找包含 U, $, k 的行，提取其中最大的数字
            job_data["salary"] = JobParser.extract_max_salary(raw_text)

            # C. 提取邮箱 (完整提取)
            # 扩大匹配范围，不仅限于 .com/.net，防止漏掉 weird domains
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.[a-zA-Z]+', raw_text)
            job_data["email"] = email_match.group(0) if email_match else ""

            # D. 提取标签 & 类型
            hashtags = re.findall(r'#(\w+)', raw_text)
            job_data["tags"] = hashtags
            for tag in hashtags:
                if "兼职" in tag: job_data["type"] = "兼职"
                if "实习" in tag: job_data["type"] = "实习"
                if "外包" in tag or "项目" in tag: job_data["type"] = "项目制"

            return job_data

        except Exception as e:
            # print(f"解析错误: {e}") # 调试用
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
        
        for page in range(5): # 最多翻5页
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = requests.get(target_url, headers=headers, proxies=PROXY, timeout=10)
                if resp.status_code != 200: break
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                divs = soup.find_all('div', class_='tgme_widget_message_wrap')
                if not divs: break

                page_jobs = []
                for div in reversed(divs):
                    job = JobParser.parse_html_message(div)
                    if job:
                        # 日期检查
                        if job['date']:
                            try:
                                d = datetime.datetime.strptime(job['date'], "%Y-%m-%d")
                                if d < cutoff_date: 
                                    self.cached_jobs = all_jobs + page_jobs
                                    return self.cached_jobs
                            except: pass
                        
                        # 有效性检查：只有标题或邮箱存在才算
                        if job['title'] != "招聘详情" or job['email']:
                            page_jobs.append(job)
                
                all_jobs.extend(page_jobs)
                print(f"    -> Page {page+1}: Found {len(page_jobs)} jobs")

                # 翻页
                link = soup.find('a', class_='tme_messages_more')
                if link and link.get('href'):
                    href = link['href']
                    target_url = href if href.startswith('http') else f"https://t.me{href}"
                    time.sleep(1)
                else:
                    break
            except Exception as e:
                print(f"[!] Err: {e}")
                break
        
        self.cached_jobs = all_jobs
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