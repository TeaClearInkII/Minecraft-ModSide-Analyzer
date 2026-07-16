import sys, asyncio, aiohttp, zipfile, json, re, shutil, platform, os, tempfile, io, hashlib
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QCheckBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QFileDialog, QTextEdit, QHeaderView, QAbstractItemView, QComboBox, QFrame, QSizePolicy, QScrollArea, QSplitter, QGroupBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QBuffer
from PyQt6.QtGui import QDesktopServices, QColor, QPixmap, QIcon, QImage

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

DEFAULT_THREADS = 10
CONFIG_FILE = BASE_DIR / "config.json"

def load_config():
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_config(data):
    try:
        existing = load_config()
        existing.update(data)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ----------------------
# 工具函数
# ----------------------
def clean_json(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)

def safe_decode(data):
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="ignore")
    return data

def sha1_hash(filepath):
    h = hashlib.sha1()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def curseforge_link(name):
    return f"https://www.curseforge.com/minecraft/mc-mods/{name}"

def mcmod_link(name):
    return f"https://search.mcmod.cn/s?key={name}"

def extract_icon_from_jar(jar_path, icon_path):
    """从jar文件中提取图标"""
    try:
        with zipfile.ZipFile(jar_path) as z:
            if icon_path in z.namelist():
                return z.read(icon_path)
    except:
        pass
    return None

def heuristic_detect(jar_path):
    try:
        cw, sw = 0, 0
        with zipfile.ZipFile(jar_path) as z:
            for name in z.namelist():
                n = name.lower()
                if "net/minecraft/client/" in n:
                    cw += 1
                    if "renderer" in n or "gui/" in n:
                        cw += 2
                if "net/minecraft/server/" in n:
                    sw += 1
                    if "dedicated" in n:
                        sw += 2
        if cw > sw + 3:
            return "client"
        if sw > cw + 3:
            return "server"
    except:
        pass
    return None

# ----------------------
# 解析 Mod
# ----------------------
def parse_fabric(text, jar_name=None):
    try:
        data = json.loads(clean_json(text))
    except:
        return None
    env = data.get("environment", "*")
    mod_id = data.get("id")
    mod_name = data.get("name") or jar_name
    icon = data.get("icon")
    return {"loader": "fabric", "id": mod_id, "name": mod_name, "env": env, "icon": icon}

def parse_quilt(text, jar_name=None):
    try:
        data = json.loads(clean_json(text))
    except:
        return None
    loader = data.get("quilt_loader", {})
    mod_id = loader.get("id")
    mod_name = loader.get("name") or jar_name
    meta_block = loader.get("metadata", {}) or {}
    icon = meta_block.get("icon")
    mc_block = data.get("minecraft", {}) or {}
    env = mc_block.get("environment", "*")
    return {"loader": "quilt", "id": mod_id, "name": mod_name, "env": env, "icon": icon}

def parse_forge(text, jar_name=None):
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except:
            return None
        mods = data.get("mods", [])
        if not mods:
            return None
        entry = mods[0]
        side = entry.get("side", "BOTH").upper()
        client_only = side == "CLIENT"
        mod_id = entry.get("modId")
        mod_name = entry.get("displayName") or jar_name
        icon = entry.get("logoFile")
        return {"loader": "forge", "client_only": client_only, "id": mod_id, "name": mod_name, "icon": icon}
    client_only = False
    for line in text.splitlines():
        if "clientOnly" in line and "true" in line.lower():
            client_only = True
    return {"loader": "forge", "client_only": client_only, "id": None, "name": jar_name, "icon": None}

def read_metadata(jar: Path):
    try:
        with zipfile.ZipFile(jar) as z:
            for info in z.infolist():
                name = info.filename
                if name.endswith("fabric.mod.json"):
                    meta = parse_fabric(safe_decode(z.read(info)), jar.stem)
                    return meta, None if meta else "fabric.mod.json解析失败"
                if name.endswith("quilt.mod.json"):
                    meta = parse_quilt(safe_decode(z.read(info)), jar.stem)
                    return meta, None if meta else "quilt.mod.json解析失败"
                if name.endswith("mods.toml"):
                    meta = parse_forge(safe_decode(z.read(info)), jar.stem)
                    return meta, None if meta else "mods.toml解析失败"
        return {"loader": None, "id": None, "name": jar.stem}, "未找到fabric.mod.json、quilt.mod.json或mods.toml"
    except Exception as e:
        return {"loader": None, "id": None, "name": jar.stem}, str(e)

def classify(meta):
    if not meta:
        return "解析失败"
    if meta.get("loader") in ("fabric", "quilt"):
        env = meta.get("env", "*")
        if env == "client":
            return "仅客户端"
        if env == "server":
            return "仅服务端"
        return "双端"
    if meta.get("loader") == "forge":
        if meta.get("client_only"):
            return "仅客户端"
        return "双端"
    return "解析失败"

CATEGORY_MAP = {"仅客户端": "纯客户端", "双端": "服务端(+双端)", "仅服务端": "服务端(+双端)"}

def cross_validate(jar_category, api_info):
    if jar_category == "解析失败" and (not api_info or api_info.get("api_status") != 200):
        return "解析失败", "低"
    api_ok = api_info and api_info.get("api_status") == 200
    if api_ok:
        cs, ss = api_info.get("client_side"), api_info.get("server_side")
        api_client = cs == "required" and ss == "unsupported"
        api_server = ss in ("required", "optional")
    jar_client = jar_category == "仅客户端"
    jar_server = jar_category in ("双端", "仅服务端")
    if api_ok and api_info.get("match_method") == "sha1":
        if jar_client and api_client:
            return "纯客户端", "高"
        if jar_server and api_server:
            return "服务端(+双端)", "高"
        if jar_client and api_server:
            return "可信度不足", "低"
        if jar_server and api_client:
            return "可信度不足", "低"
        if api_client:
            return "纯客户端", "高"
        if api_server:
            return "服务端(+双端)", "高"
        return "可信度不足", "中"
    if api_ok and api_info.get("match_method") == "fuzzy":
        if (jar_client and api_client) or (jar_server and api_server):
            return CATEGORY_MAP.get(jar_category, "解析失败"), "中"
        if (jar_client and not api_client) or (jar_server and not api_server):
            return "可信度不足", "低"
    if jar_category != "解析失败":
        return CATEGORY_MAP.get(jar_category, "解析失败"), "中"
    if api_ok:
        if api_client:
            return "纯客户端", "中"
        if api_server:
            return "服务端(+双端)", "中"
    return "解析失败", "低"

async def modrinth_version_info(jar_path, meta, session):
    result = {"match_method": None, "client_side": None, "server_side": None,
              "project_id": None, "version": None, "slug": None, "api_status": None}
    try:
        jar_hash = sha1_hash(jar_path)
        url = f"https://api.modrinth.com/v2/version_file/{jar_hash}?algorithm=sha1"
        async with session.get(url, timeout=5) as r:
            if r.status == 200:
                data = await r.json()
                pid = data.get("project_id")
                ver = data.get("version_number")
                async with session.get(f"https://api.modrinth.com/v2/project/{pid}", timeout=5) as pr:
                    if pr.status == 200:
                        pdata = await pr.json()
                        result.update(match_method="sha1",
                                     client_side=pdata.get("client_side"),
                                     server_side=pdata.get("server_side"),
                                     slug=pdata.get("slug"),
                                     project_id=pid, version=ver, api_status=200)
                    else:
                        result.update(match_method="sha1", project_id=pid,
                                     version=ver, api_status=pr.status)
                return result
            result["api_status"] = r.status
    except:
        result["api_status"] = None
    query = meta.get("id") or meta.get("name") if meta else None
    if not query:
        return result
    try:
        url = "https://api.modrinth.com/v2/search"
        params = {"query": query, "facets": '[["project_type:mod"]]'}
        async with session.get(url, params=params, timeout=5) as r:
            if r.status != 200:
                return result
            data = await r.json()
            hits = data.get("hits", [])
            best, best_score = None, 0
            for h in hits:
                score = 0
                if meta and meta.get("id") and h.get("slug") == meta["id"]:
                    score += 100
                score += similarity(meta.get("name", ""), h.get("title", "")) * 50
                if score > best_score:
                    best, best_score = h, score
            if best_score >= 60:
                result.update(match_method="fuzzy", client_side=best.get("client_side"),
                             server_side=best.get("server_side"), project_id=best.get("project_id"),
                             slug=best.get("slug"), api_status=200)
    except:
        pass
    return result

# ----------------------
# Worker线程
# ----------------------
class ModAnalyzerThread(QThread):
    update_progress = pyqtSignal(int,int)
    log_signal = pyqtSignal(str,str)
    mod_signal = pyqtSignal(dict)
    finished_dir = pyqtSignal(Path)

    def __init__(self, mods_dir, gen_folder=True, gen_log=True, max_threads=DEFAULT_THREADS):
        super().__init__()
        self.mods_dir = mods_dir
        self.gen_folder = gen_folder
        self.gen_log = gen_log
        self.max_threads = max_threads
        self.output_dir = None
        self.all_mods = []
        self.total_jars = 0

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.analyze_mods())
        finally:
            loop.close()

    async def analyze_mods(self):
        jars = list(self.mods_dir.glob("*.jar"))
        self.total_jars = len(jars)
        if self.total_jars == 0:
            self.log_signal.emit("❌ 未找到 Jar 文件","red")
            return

        date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_dir = BASE_DIR / f"{date_str}_分析"
        self.output_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        dirs = {
            "纯客户端": out_dir / "纯客户端",
            "服务端(+双端)": out_dir / "服务端(+双端)",
            "可信度不足": out_dir / "可信度不足",
            "解析失败": out_dir / "解析失败",
        }
        if self.gen_folder:
            for d in dirs.values():
                d.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(self.max_threads)
        async with aiohttp.ClientSession() as session:
            tasks = []
            for jar in jars:
                tasks.append(self.process_jar_with_sem(jar, session, sem, dirs))
            await asyncio.gather(*tasks)

        # 生成日志文件
        if self.gen_log:
            sections = {"纯客户端":[], "服务端(+双端)":[], "可信度不足":[], "解析失败":[]}
            for m in self.all_mods:
                sections.setdefault(m["category"], []).append(m)

            log_file = out_dir / f"{date_str}_分析.txt"
            with open(log_file,"w",encoding="utf-8") as f:
                f.write("===== 重要提示 =====\n")
                f.write("1. 端属分类综合了JAR解析和Modrinth API校验\n")
                f.write("2. 置信度高表示JAR和API结果一致；中表示仅单源数据\n")
                f.write("3. 最终分类建议结合官方文档或实际测试确认\n\n")

                f.write("===== 作者信息 =====\n")
                f.write("作者: 茶清墨刂\n")
                f.write("主页: https://space.bilibili.com/388428308\n")
                f.write("GitHub: https://github.com/TeaClearInkII/Minecraft-ModSide-Analyzer\n")
                f.write("使用AI进行开发\n")
                f.write(f"版本: 0.5.0-2026.7.16\n\n")

                side_text = {"required": "需要", "optional": "可选", "unsupported": "不支持"}
                for sec in ["纯客户端","服务端(+双端)","可信度不足","解析失败"]:
                    f.write(f"\n===== {sec} =====\n\n")
                    for m in sorted(sections[sec], key=lambda x:x["name"].lower()):
                        jc = m.get("jar_category", "?")
                        ai = m.get("api_info")
                        if ai and ai.get("api_status") == 200:
                            mm = {"sha1": "MR精确", "fuzzy": "MR搜索"}.get(ai.get("match_method"), "?")
                            cs = side_text.get(ai.get("client_side"), "?")
                            ss = side_text.get(ai.get("server_side"), "?")
                            api_str = f"API:{mm} 客户端:{cs} 服务端:{ss}"
                        else:
                            api_str = "API:无"
                        f.write(f"[{m.get('confidence','?')}] {m['name']}  |  JAR:{jc}  {api_str}")
                        if m.get("error"):
                            f.write(f"  错误:{m['error']}")
                        f.write(f"\n  CurseForge:{m['links']['curseforge']}  Modrinth:{m['links']['modrinth']}  MC百科:{m['links']['mcmod']}\n\n")

            json_file = out_dir / f"{date_str}_分析.json"
            summary = {}
            for cat in sections:
                summary[cat] = len(sections[cat])
            json_data = {
                "tool": "Minecraft Mod 分析工具",
                "version": "0.5.0",
                "date": date_str,
                "summary": summary,
                "mods": [{
                    "file": m["name"],
                    "final_category": m["category"],
                    "confidence": m.get("confidence"),
                    "jar_analysis": {
                        "parser": None,
                        "jar_category": m.get("jar_category"),
                        "heuristic": m.get("heuristic"),
                    },
                    "api_analysis": {
                        "match_method": None,
                        "client_side": None,
                        "server_side": None,
                        "project_id": None,
                    } if not (m.get("api_info") and m["api_info"].get("api_status") == 200) else {
                        "match_method": m["api_info"].get("match_method"),
                        "client_side": m["api_info"].get("client_side"),
                        "server_side": m["api_info"].get("server_side"),
                        "project_id": m["api_info"].get("project_id"),
                    },
                    "links": m.get("links"),
                    "error": m.get("error"),
                } for m in self.all_mods],
            }
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)

        self.finished_dir.emit(out_dir)

    async def process_jar_with_sem(self, jar, session, sem, dirs):
        async with sem:
            try:
                meta, error = read_metadata(jar)
                jar_category = classify(meta)
                heuristic = None
                if jar_category in ("双端", "解析失败"):
                    heuristic = heuristic_detect(jar)
                    if heuristic == "client":
                        jar_category = "仅客户端"
                    elif heuristic == "server":
                        jar_category = "仅服务端"

                api_info = await modrinth_version_info(jar, meta, session)
                final_category, confidence = cross_validate(jar_category, api_info)

                if api_info:
                    mr_link = (f"https://modrinth.com/mod/{api_info['slug']}" if api_info.get("slug")
                               else f"https://modrinth.com/mod/{api_info['project_id']}" if api_info.get("project_id")
                               else None)
                else:
                    mr_link = None
                name_for_link = meta.get("name") if meta else jar.stem
                cf_link = curseforge_link(name_for_link)
                mc_link = mcmod_link(name_for_link)
                links = {"curseforge": cf_link, "modrinth": mr_link, "mcmod": mc_link}

                log_color = {"纯客户端":"orange","服务端(+双端)":"green","可信度不足":"gold"}.get(final_category, "red")
                status_text = f"{final_category} [{confidence}]"
                if error:
                    status_text += f" ({error})"
                self.log_signal.emit(f"{jar.name} → {status_text}", log_color)

                loader = meta.get("loader") if meta else None
                mod_info = {"name": jar.name, "category": final_category, "jar_category": jar_category,
                           "confidence": confidence, "api_info": api_info, "links": links,
                           "error": error, "icon": None, "icon_data": None, "heuristic": heuristic,
                           "loader": loader}

                if meta and meta.get("icon"):
                    icon_data = extract_icon_from_jar(jar, meta["icon"])
                    if icon_data:
                        mod_info["icon_data"] = icon_data

                self.all_mods.append(mod_info)
                self.mod_signal.emit(mod_info)

                if self.gen_folder:
                    target_dir = dirs.get(final_category)
                    if target_dir:
                        try:
                            shutil.copy2(jar, target_dir / jar.name)
                        except Exception as e:
                            self.log_signal.emit(f"⚠ 复制失败: {jar.name} → {e}", "red")
            finally:
                self.update_progress.emit(len(self.all_mods), self.total_jars)

class ClickableLabel(QLabel):
    """可点击的标签，用于显示超链接"""
    def __init__(self, text, url, parent=None):
        super().__init__(parent)
        self.url = url
        self.setText(f'<a href="{url}" style="color: #0066cc; text-decoration: none;">{text}</a>')
        self.setOpenExternalLinks(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

# ----------------------
# GUI
# ----------------------
class ModAnalyzerGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Mod 分析工具 v0.5.0")
        self.resize(1600, 900)
        self.setAcceptDrops(True)
        self.all_mods = []

        # 主布局
        main_layout = QVBoxLayout()

        # -------------------
        # 控制面板区域
        control_frame = QFrame()
        control_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        control_layout = QVBoxLayout(control_frame)
        
        # 添加提示信息
        tip_label = QLabel("⚠️ 提示: 分类综合JAR解析+Modrinth API双重校验。置信度: 高(双源一致) / 中(仅单源) / 低(冲突)。最终分类建议结合官方文档确认")
        tip_label.setStyleSheet("color: #FF9800; font-weight: bold; padding: 5px; background-color: #FFF3CD; border: 1px solid #FFEAA7; border-radius: 3px;")
        tip_label.setWordWrap(True)
        control_layout.addWidget(tip_label)
        
        # 作者信息栏
        author_frame = QFrame()
        author_frame.setStyleSheet("QFrame { background-color: #E8F4FD; border: 1px solid #B8D4E8; border-radius: 4px; padding: 4px; }")
        author_layout = QHBoxLayout(author_frame)
        author_layout.setContentsMargins(8, 4, 8, 4)
        
        author_info = QLabel("作者: 茶清墨刂 | 版本: 0.5.0-2026.7.16 | 使用AI进行开发 | ")
        author_info.setStyleSheet("color: #2C3E50; font-size: 12px; font-weight: bold;")
        
        homepage_label = ClickableLabel("B站主页", "https://space.bilibili.com/388428308")
        homepage_label.setStyleSheet("color: #2980B9; font-size: 12px; font-weight: bold;")
        
        github_label = ClickableLabel("GitHub", "https://github.com/TeaClearInkII/Minecraft-ModSide-Analyzer")
        github_label.setStyleSheet("color: #2980B9; font-size: 12px; font-weight: bold;")
        
        separator = QLabel("|")
        separator.setStyleSheet("color: #7F8C8D; font-size: 12px; padding: 0 5px;")
        
        author_layout.addWidget(author_info)
        author_layout.addWidget(homepage_label)
        author_layout.addWidget(separator)
        author_layout.addWidget(github_label)
        author_layout.addStretch()
        
        control_layout.addWidget(author_frame)
        
        # 文件夹输入
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("Minecraft 模组文件夹:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("可拖入文件夹或手动输入路径，或点击浏览选择")
        folder_layout.addWidget(self.folder_edit)
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.browse_btn)
        control_layout.addLayout(folder_layout)

        # 线程选择
        thread_layout = QHBoxLayout()
        thread_layout.addWidget(QLabel("并发线程数:"))
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1,20)
        cfg = load_config()
        self.thread_spin.setValue(cfg.get("threads", DEFAULT_THREADS))
        self.thread_spin.valueChanged.connect(lambda v: save_config({"threads": v}))
        thread_layout.addWidget(self.thread_spin)
        control_layout.addLayout(thread_layout)

        # 生成选项
        options_layout = QHBoxLayout()
        self.gen_log_cb = QCheckBox("生成日志文件")
        self.gen_log_cb.setChecked(cfg.get("gen_log", True))
        self.gen_log_cb.toggled.connect(lambda v: save_config({"gen_log": v}))
        self.gen_folder_cb = QCheckBox("生成分类文件夹")
        self.gen_folder_cb.setChecked(cfg.get("gen_folder", True))
        self.gen_folder_cb.toggled.connect(lambda v: save_config({"gen_folder": v}))
        options_layout.addWidget(self.gen_log_cb)
        options_layout.addWidget(self.gen_folder_cb)
        options_layout.addStretch()
        control_layout.addLayout(options_layout)

        # 按钮和进度条
        button_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始解析")
        self.start_btn.clicked.connect(self.start_analysis)
        button_layout.addWidget(self.start_btn)
        
        self.open_dir_btn = QPushButton("打开生成目录")
        self.open_dir_btn.clicked.connect(self.open_output_dir)
        self.open_dir_btn.setEnabled(False)
        button_layout.addWidget(self.open_dir_btn)
        
        self.open_root_btn = QPushButton("打开分析目录")
        self.open_root_btn.clicked.connect(self.open_analysis_root)
        button_layout.addWidget(self.open_root_btn)
        
        button_layout.addStretch()
        
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(300)
        button_layout.addWidget(self.progress)
        
        control_layout.addLayout(button_layout)

        # 日志框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        control_layout.addWidget(self.log_text)
        
        main_layout.addWidget(control_frame)

        # -------------------
        # 表格区域 - 使用QSplitter左右布局
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 创建三个分类的表格
        self.tables = {}
        categories = [
            ("纯客户端", "orange", "仅可在客户端运行的模组 (服务端无效)"),
            ("服务端(+双端)", "green", "可在服务端运行的模组 (含双端和仅服务端)"),
            ("可信度不足", "gold", "JAR解析与API校验冲突或数据不足"),
            ("解析失败", "red", "无法解析或读取失败的模组"),
        ]
        
        for cat_name, color, description in categories:
            # 创建分组框
            group_box = QGroupBox(cat_name)
            display_color = "teal" if color == "teal" else color
            group_box.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {display_color}; font-size: 14px; }}")
            
            # 创建布局
            group_layout = QVBoxLayout()
            
            # 添加描述标签
            desc_label = QLabel(description)
            desc_label.setStyleSheet(f"color: {display_color}; font-size: 12px; padding-bottom: 5px;")
            group_layout.addWidget(desc_label)
            
            # 创建表格
            table = QTableWidget()
            table.setColumnCount(3)
            table.setHorizontalHeaderLabels(["图标", "Mod 名称", "解析"])
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(0, 40)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            table.setSortingEnabled(True)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.setAlternatingRowColors(True)  # 交替行颜色
            
            group_layout.addWidget(table)
            group_box.setLayout(group_layout)
            
            self.splitter.addWidget(group_box)
            self.tables[cat_name] = table
        
        # 设置分割器各部分的初始大小(左右布局=像素宽度)
        self.splitter.setSizes([350, 350, 350, 350])
        main_layout.addWidget(self.splitter, 1)  # 1表示可拉伸

        self.setLayout(main_layout)
        self.output_dir = None
        self.mods_dir = None

    # 拖拽
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            folder = urls[0].toLocalFile()
            path = Path(folder)
            if path.is_dir():
                self.mods_dir = path
                self.folder_edit.setText(str(self.mods_dir))

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self,"选择 Mods 文件夹")
        if folder:
            self.mods_dir = Path(folder)
            self.folder_edit.setText(str(self.mods_dir))

    def start_analysis(self):
        folder_text = self.folder_edit.text().strip()
        if folder_text:
            path = Path(folder_text)
            if not path.is_dir():
                self.log_text.append("❌ 路径无效")
                return
            self.mods_dir = path
        if not self.mods_dir:
            self.log_text.append("❌ 请先选择 Mods 文件夹")
            return

        # 清空所有表格
        for table in self.tables.values():
            table.setRowCount(0)
        
        self.progress.setValue(0)
        self.output_dir = None
        self.open_dir_btn.setEnabled(False)
        self.log_text.clear()

        self.worker = ModAnalyzerThread(
            self.mods_dir,
            self.gen_folder_cb.isChecked(),
            self.gen_log_cb.isChecked(),
            self.thread_spin.value()
        )
        self.worker.update_progress.connect(self.on_progress)
        self.worker.log_signal.connect(self.on_log)
        self.worker.mod_signal.connect(self.on_mod)
        self.worker.finished_dir.connect(self.set_output_dir)
        self.worker.start()

    def on_progress(self, current, total):
        if total > 0:
            percent = min(int(current / total * 100), 100)
            self.progress.setFormat(f"[{current}/{total}] {percent}%")
            self.progress.setValue(percent)

    def on_log(self, msg, color):
        color_map = {"red": Qt.GlobalColor.red, "green": Qt.GlobalColor.green,
                     "blue": Qt.GlobalColor.blue, "orange": Qt.GlobalColor.darkYellow,
                     "gold": Qt.GlobalColor.darkYellow}
        self.log_text.setTextColor(color_map.get(color, Qt.GlobalColor.darkYellow))
        self.log_text.append(msg)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    def on_mod(self, mod_info):
        table = self.tables.get(mod_info["category"], self.tables["解析失败"])
        row = table.rowCount()
        table.insertRow(row)
        
        # 图标列
        icon_item = QTableWidgetItem()
        if mod_info.get("icon_data"):
            try:
                # 将字节数据转换为QPixmap
                pixmap = QPixmap()
                pixmap.loadFromData(mod_info["icon_data"])
                
                # 缩放图标到合适大小（减小图标尺寸）
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    icon_item.setData(Qt.ItemDataRole.DecorationRole, scaled_pixmap)
            except Exception as e:
                print(f"图标加载失败: {e}")
        
        table.setItem(row, 0, icon_item)

        # 名称列（含按钮第二行）
        name_widget = QWidget()
        nvbox = QVBoxLayout(name_widget)
        nvbox.setContentsMargins(4, 2, 4, 2)
        nvbox.setSpacing(2)

        name_label = QLabel(mod_info["name"])
        cat_color = {"纯客户端": "orange", "服务端(+双端)": "green", "可信度不足": "gold"}
        fg_color = QColor(cat_color.get(mod_info["category"], "red"))
        name_label.setStyleSheet(f"color: {fg_color.name()}; font-weight: bold;")
        nvbox.addWidget(name_label)

        tooltip = f"最终: {mod_info['category']} | 置信度: {mod_info.get('confidence', '?')}"
        if mod_info.get("jar_category"):
            tooltip += f"\nJAR解析: {mod_info['jar_category']}"
        if mod_info.get("loader"):
            tooltip += f" ({mod_info['loader']})"
        ai = mod_info.get("api_info")
        if ai and ai.get("api_status") == 200:
            mm = {"sha1": "MR精确", "fuzzy": "MR搜索"}.get(ai.get("match_method"), "?")
            cs = {"required": "需要", "optional": "可选", "unsupported": "不支持"}.get(ai.get("client_side"), "?")
            ss = {"required": "需要", "optional": "可选", "unsupported": "不支持"}.get(ai.get("server_side"), "?")
            tooltip += f"\nModrinth: {mm}匹配 客户端:{cs} 服务端:{ss}"
        if mod_info.get("error"):
            tooltip += f"\n错误: {mod_info['error']}"
        name_label.setToolTip(tooltip)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)
        for key, display in [("curseforge","CF"), ("modrinth","MR"), ("mcmod","MC百科")]:
            btn = QPushButton(display)
            url = mod_info["links"].get(key)
            if url:
                btn.clicked.connect(lambda checked, url=url: QDesktopServices.openUrl(QUrl(url)))
                btn.setToolTip(url)
            else:
                btn.setEnabled(False)
                btn.setToolTip("无链接")
            btn.setMaximumWidth(70)
            btn.setMaximumHeight(22)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        nvbox.addLayout(btn_row)
        table.setCellWidget(row, 1, name_widget)

        # 解析列
        jc = mod_info.get("jar_category", "?")
        ai = mod_info.get("api_info")
        side_text = {"required": "需要", "optional": "可选", "unsupported": "不支持"}
        if ai and ai.get("api_status") == 200:
            mm = {"sha1": "MR精确", "fuzzy": "MR搜索"}.get(ai.get("match_method"), "?")
            cs = side_text.get(ai.get("client_side"), "?")
            ss = side_text.get(ai.get("server_side"), "?")
            api_str = f"{mm}\n客户端:{cs} 服务端:{ss}"
        else:
            api_str = "无API数据"
        conf = mod_info.get("confidence", "?")
        status_text = f"[{conf}] {jc}\n{api_str}"
        status_item = QTableWidgetItem(status_text)
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        conf_color = {"高": QColor("green"), "中": QColor("gold"), "低": QColor("red")}
        status_item.setForeground(conf_color.get(conf, QColor("gray")))
        table.setItem(row, 2, status_item)

    def set_output_dir(self, path:Path):
        self.output_dir = path
        self.open_dir_btn.setEnabled(True)
        self.log_text.append(f"\n✅ 分析完成！输出目录: {path}")

    def open_analysis_root(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BASE_DIR)))

    def open_output_dir(self):
        if self.output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

# ----------------------
# 启动
# ----------------------
if __name__=="__main__":
    app = QApplication(sys.argv)
    gui = ModAnalyzerGUI()
    gui.show()
    sys.exit(app.exec())
