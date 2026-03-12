import os
import csv
import time
import yaml
import subprocess
import re
from datetime import datetime
from playwright.sync_api import sync_playwright

# --- 配置加载 ---
try:
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print("❌ 错误：找不到 config.yaml 文件，请确认已创建。")
    exit()

def is_direct_link(url):
    """判断是否为直接下载链接 (dmg, zip, pkg 等)"""
    if not url:
        return False
    direct_extensions = ['.dmg', '.pkg', '.zip', '.rar', '.7z', '.tar.gz', '.exe']
    return any(url.lower().endswith(ext) for ext in direct_extensions)

def download_with_aria2(url, save_dir, filename):
    """调用 Aria2 进行多线程下载"""
    os.makedirs(save_dir, exist_ok=True)
    
    # 构造 aria2c 命令
    # -x 16: 16线程, -s 16: 16分片, -k 1M: 最小分片大小
    cmd = [
        "aria2c",
        "-x", "16",
        "-s", "16",
        "-k", "1M",
        "-o", filename,
        "-d", save_dir,
        "--allow-overwrite=true",
        "--console-log-level=warn", # 减少输出噪音
        url
    ]
    
    print(f"   ⬇️  正在使用 Aria2 高速下载: {filename}")
    try:
        # 执行命令，设置超时时间为 30 分钟
        result = subprocess.run(cmd, timeout=1800, capture_output=True, text=True, encoding='utf-8')
        
        if result.returncode == 0:
            file_path = os.path.join(save_dir, filename)
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                return True, file_path, size
            else:
                return False, "文件未生成", 0
        else:
            return False, f"Aria2 错误码:{result.returncode}", 0
    except subprocess.TimeoutExpired:
        return False, "下载超时 (超过30分钟)", 0
    except Exception as e:
        return False, str(e), 0

def get_software_list(page, url):
    """获取列表页的软件详情页链接"""
    page.goto(url)
    links = []
    
    # 尝试多种常见的博客文章标题选择器 (适配不同主题)
    selectors = [
        'h2 a', 
        '.post-title a', 
        'article h1 a', 
        '.entry-title a', 
        'h3 a',
        '.blog-post-title a'
    ]
    
    elements = []
    for sel in selectors:
        elements = page.query_selector_all(sel)
        if elements:
            break
    
    count = 0
    for el in elements:
        if count >= config['max_software']:
            break
        href = el.get_attribute('href')
        if href and href.startswith('http'):
            links.append(href)
            count += 1
            
    return links

def parse_detail(page, url):
    """进入详情页提取信息"""
    page.goto(url)
    
    # 1. 提取名称
    name_el = page.query_selector('h1') or page.query_selector('.post-title') or page.query_selector('.entry-title')
    name = name_el.inner_text() if name_el else "未知软件"
    
    # 2. 提取分类
    cat_el = page.query_selector('.category a') or page.query_selector('.meta-cat a') or page.query_selector('.tags a')
    category = cat_el.inner_text() if cat_el else "未分类"
    
    # 3. 提取介绍 (取前 300 字)
    desc_el = page.query_selector('.entry-content') or page.query_selector('.post-content') or page.query_selector('.content')
    description = ""
    if desc_el:
        description = desc_el.inner_text().replace('\n', ' ').strip()[:300]
    
    # 4. 提取下载链接 (核心逻辑)
    download_link = ""
    extract_code = ""
    link_type = "unknown"
    
    # 获取所有链接
    buttons = page.query_selector_all('a')
    for btn in buttons:
        href = btn.get_attribute('href')
        text = btn.inner_text()
        if not href: continue
        
        # 优先级 1: 直链 (最宝贵，直接下载)
        if is_direct_link(href):
            download_link = href
            link_type = "direct"
            break 
        
        # 优先级 2: 百度网盘
        elif 'pan.baidu.com' in href or 'baidu.com/s/' in href:
            download_link = href
            link_type = "baidu"
            # 尝试从按钮文字或周围文本提取提取码
            match = re.search(r'(提取码|密码)[:：\s]*([a-zA-Z0-9]{4})', text)
            if match: 
                extract_code = match.group(2)
            break 
            
        # 优先级 3: 123 云盘
        elif '123pan.com' in href:
            download_link = href
            link_type = "123pan"
            break
            
        # 优先级 4: 蓝奏云
        elif 'lanzou' in href or 'lanzhou' in href:
            download_link = href
            link_type = "lanzou"
            break

    return {
        "name": name.strip(),
        "category": category.strip(),
        "description": description,
        "download_link": download_link,
        "link_type": link_type,
        "extract_code": extract_code,
        "url": url
    }

def main():
    results = []
    save_path = config.get('save_path', './downloads')
    os.makedirs(save_path, exist_ok=True)

    print("🌐 正在启动自动化浏览器...")
    
    with sync_playwright() as p:
        # headless=False 让你能看到浏览器在自动操作，方便调试
        # 如果想在后台跑，改成 headless=True
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # 伪装 User-Agent，防止被简单反爬拦截
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        })

        list_url = config['target_url']
        print(f"📋 正在访问目标网站: {list_url}")
        
        detail_urls = []
        try:
            detail_urls = get_software_list(page, list_url)
            if not detail_urls:
                print("⚠️ 未找到任何软件链接，可能是网站结构变化或选择器不匹配。")
            else:
                print(f"✅ 成功找到 {len(detail_urls)} 个待处理软件。")
        except Exception as e:
            print(f"❌ 获取列表失败: {e}")

        for i, url in enumerate(detail_urls):
            print(f"\n[{i+1}/{len(detail_urls)}] 正在处理: {url}")
            try:
                info = parse_detail(page, url)
                print(f"   📦 软件名称: {info['name']}")
                print(f"   🔗 链接类型: {info['link_type']}")
                
                success = False
                path = "N/A"
                size = 0
                status = "失败"
                
                if info['link_type'] == 'direct':
                    # 自动下载直链
                    raw_filename = os.path.basename(info['download_link'].split('?')[0])
                    if not raw_filename or len(raw_filename) < 3:
                        raw_filename = "download_file.zip"
                    # 简单清理文件名中的非法字符
                    safe_filename = "".join(c for c in raw_filename if c.isalnum() or c in ('.', '_', '-', ' ')).strip()
                    
                    success, path, size = download_with_aria2(info['download_link'], save_path, safe_filename)
                    status = "下载完成" if success else "下载失败"
                
                elif info['link_type'] in ['baidu', '123pan', 'lanzou']:
                    # 网盘链接处理策略
                    print(f"   ⚠️  检测到网盘资源 ({info['link_type']})。")
                    print(f"   💡  V1.0 策略：已记录链接和提取码，需手动下载。")
                    path = f"链接:{info['download_link']} | 码:{info['extract_code']}"
                    status = "待手动下载 (网盘)"
                    success = True # 视为任务已记录成功
                    
                else:
                    path = "未找到有效下载链接"
                    status = "无链接"

                results.append({
                    "软件名称": info['name'],
                    "分类": info['category'],
                    "介绍": info['description'],
                    "链接类型": info['link_type'],
                    "下载地址/备注": path,
                    "提取码": info['extract_code'],
                    "文件大小(MB)": round(size / 1024 / 1024, 2) if size > 0 else 0,
                    "处理时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "状态": status
                })
                
                # 礼貌等待 2-5 秒，防止被封 IP
                time.sleep(2)
                
            except Exception as e:
                print(f"   ❌ 处理过程中发生错误: {e}")
                results.append({
                    "软件名称": "Error",
                    "分类": "Error",
                    "介绍": str(e),
                    "链接类型": "Error",
                    "下载地址/备注": "N/A",
                    "提取码": "",
                    "文件大小(MB)": 0,
                    "处理时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "状态": "程序异常"
                })
        
        browser.close()

    # 生成 CSV 报表
    csv_file = config['csv_output']
    fieldnames = ["软件名称", "分类", "介绍", "链接类型", "下载地址/备注", "提取码", "文件大小(MB)", "处理时间", "状态"]
    
    with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print("\n" + "="*50)
    print(f"🎉 任务全部完成！")
    print(f"📊 详细报表已保存至: {os.path.abspath(csv_file)}")
    print(f"📂 直链软件已下载至: {os.path.abspath(save_path)}")
    print("="*50)

if __name__ == "__main__":
    # 检查依赖
    try:
        import yaml
        import playwright
    except ImportError:
        print("❌ 缺少必要的 Python 库！")
        print("请在命令行运行以下命令安装:")
        print("   pip install playwright pyyaml")
        exit()
    
    main()