import sys, asyncio, aiohttp, zipfile, json, re, shutil, platform, os, tempfile, io
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QCheckBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QFileDialog, QTextEdit, QHeaderView, QAbstractItemView, QComboBox, QFrame, QSizePolicy, QScrollArea, QSplitter, QGroupBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QBuffer
from PyQt6.QtGui import QDesktopServices, QColor, QPixmap, QIcon, QImage

# ----------------------
# 工具函数
# ----------------------
def clean_json(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)

def safe_decode(data):
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="ignore")
    return data

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

def parse_forge(text, jar_name=None):
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
                if name.endswith("mods.toml"):
                    meta = parse_forge(safe_decode(z.read(info)), jar.stem)
                    return meta, None if meta else "mods.toml解析失败"
        return {"loader": None, "id": None, "name": jar.stem}, "未找到mods.toml或fabric.mod.json"
    except Exception as e:
        return {"loader": None, "id": None, "name": jar.stem}, str(e)

def classify(meta):
    if not meta:
        return "解析失败"
    if meta.get("loader") == "fabric":
        if meta.get("env") == "client":
            return "仅客户端"
        return "服务端"
    if meta.get("loader") == "forge":
        if meta.get("client_only"):
            return "仅客户端"
        return "服务端"
    if meta.get("loader") is None:
        return "解析失败"
    return "服务端"

async def modrinth_link(meta, session):
    query = meta.get("id") or meta.get("name")
    if not query:
        return None
    try:
        url = "https://api.modrinth.com/v2/search"
        params = {"query": query, "facets": '[["project_type:mod"]]'}
        async with session.get(url, params=params, timeout=5) as r:
            if r.status != 200:
                return None
            data = await r.json()
            hits = data.get("hits", [])
            best = None
            best_score = 0
            for h in hits:
                score = 0
                if meta.get("id") and h.get("slug") == meta["id"]:
                    score += 100
                score += similarity(meta.get("name", ""), h.get("title", "")) * 50
                if score > best_score:
                    best = h
                    best_score = score
            if best_score >= 60:
                return f"https://modrinth.com/mod/{best['slug']}"
    except:
        return None
    return None

# ----------------------
# Worker线程
# ----------------------
class ModAnalyzerThread(QThread):
    update_progress = pyqtSignal(int,int)
    log_signal = pyqtSignal(str,str)
    mod_signal = pyqtSignal(dict)
    finished_dir = pyqtSignal(Path)

    def __init__(self, mods_dir, gen_folder=True, gen_log=True, max_threads=5):
        super().__init__()
        self.mods_dir = mods_dir
        self.gen_folder = gen_folder
        self.gen_log = gen_log
        self.max_threads = max_threads
        self.output_dir = None
        self.all_mods = []

    def run(self):
        asyncio.run(self.analyze_mods())

    async def analyze_mods(self):
        jars = list(self.mods_dir.glob("*.jar"))
        total = len(jars)
        if total == 0:
            self.log_signal.emit("❌ 未找到 Jar 文件","red")
            return

        date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_dir = Path(__file__).parent / f"{date_str}_分析"
        self.output_dir = out_dir
        client_dir = out_dir / "仅客户端"
        server_dir = out_dir / "服务端"
        fail_dir = out_dir / "解析失败"
        if self.gen_folder:
            client_dir.mkdir(parents=True, exist_ok=True)
            server_dir.mkdir(parents=True, exist_ok=True)
            fail_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(self.max_threads)
        async with aiohttp.ClientSession() as session:
            tasks = []
            for jar in jars:
                tasks.append(self.process_jar_with_sem(jar, session, sem, client_dir, server_dir, fail_dir))
            await asyncio.gather(*tasks)

        # 生成日志文件
        if self.gen_log:
            sections = {"服务端":[], "仅客户端":[], "解析失败":[]}
            for m in self.all_mods:
                sections[m["category"]].append(m)

            log_file = out_dir / f"{date_str}_分析.txt"
            with open(log_file,"w",encoding="utf-8") as f:
                # 在日志文件开头添加提示信息和作者信息
                f.write("===== 重要提示 =====\n")
                f.write("1. 端属分类是从模组文件中解析，可靠性较高，但不一定完全准确\n")
                f.write("2. 由于未使用官方API，模组页面链接（CurseForge、Modrinth）可能不准确\n")
                f.write("3. 最终分类建议结合官方文档或实际测试确认\n\n")
                
                # 添加作者信息
                f.write("===== 作者信息 =====\n")
                f.write("作者: 茶清墨刂\n")
                f.write("主页: https://space.bilibili.com/388428308\n")
                f.write("GitHub: https://github.com/chaqingmodiao/minecraft-mod-analyzer\n")
                f.write("使用AI进行开发\n")
                f.write(f"版本: 0.3.0-2026.1.3\n\n")
                
                for sec in ["服务端","仅客户端","解析失败"]:
                    f.write(f"\n===== {sec} =====\n\n")
                    for m in sorted(sections[sec], key=lambda x:x["name"].lower()):
                        f.write(f"[{sec}] {m['name']}")
                        if m.get("error"):
                            f.write(f" ({m['error']})")
                        f.write(f" | CF: {m['links']['curseforge']}")
                        f.write(f" | MR: {m['links']['modrinth']}")
                        f.write(f" | MC: {m['links']['mcmod']}\n")

        self.finished_dir.emit(out_dir)

    async def process_jar_with_sem(self, jar, session, sem, client_dir, server_dir, fail_dir):
        async with sem:
            try:
                meta, error = read_metadata(jar)
                category = classify(meta)
                name_for_link = meta.get("name") if meta else jar.stem
                cf_link = curseforge_link(name_for_link)
                mc_link = mcmod_link(name_for_link)
                mr_link = await modrinth_link(meta, session) if meta else None
                links = {"curseforge": cf_link, "modrinth": mr_link, "mcmod": mc_link}

                color = "green" if category=="服务端" else "orange" if category=="仅客户端" else "red"
                status_text = f"{category}" if not error else f"{category} ({error})"
                self.log_signal.emit(f"{jar.name} → {status_text}", color)

                mod_info = {"name": jar.name, "category": category, "links": links, "error": error, "icon": None, "icon_data": None}
                
                # 提取图标数据
                if meta and meta.get("icon"):
                    icon_data = extract_icon_from_jar(jar, meta["icon"])
                    if icon_data:
                        mod_info["icon_data"] = icon_data
                
                self.all_mods.append(mod_info)
                self.mod_signal.emit(mod_info)

                if self.gen_folder:
                    try:
                        if category=="仅客户端":
                            shutil.copy2(jar, client_dir / jar.name)
                        elif category=="服务端":
                            shutil.copy2(jar, server_dir / jar.name)
                        else:
                            shutil.copy2(jar, fail_dir / jar.name)
                    except Exception as e:
                        self.log_signal.emit(f"⚠ 复制失败: {jar.name} → {e}", "red")
            finally:
                self.update_progress.emit(len(self.all_mods), len(list(self.mods_dir.glob("*.jar"))))

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
        self.setWindowTitle("Minecraft Mod 分析工具 v0.3.0")
        self.resize(1400, 1000)
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
        tip_label = QLabel("⚠️ 提示: 端属从模组文件中解析，可靠性高，但不一定正确。由于未使用官方API，模组页面链接（CurseForge、Modrinth）可能不准确。最终分类建议结合官方文档或实际测试确认")
        tip_label.setStyleSheet("color: #FF9800; font-weight: bold; padding: 5px; background-color: #FFF3CD; border: 1px solid #FFEAA7; border-radius: 3px;")
        tip_label.setWordWrap(True)
        control_layout.addWidget(tip_label)
        
        # 作者信息栏
        author_layout = QHBoxLayout()
        
        # 作者和版本信息
        author_info = QLabel("作者: 茶清墨刂 | 版本: 0.3.0-2026.1.3 | 使用AI进行开发 | ")
        author_info.setStyleSheet("color: #666; font-size: 11px;")
        
        # 主页链接
        homepage_label = ClickableLabel("B站主页", "https://space.bilibili.com/388428308")
        homepage_label.setStyleSheet("color: #666; font-size: 11px;")
        
        # GitHub链接
        github_label = ClickableLabel("GitHub", "https://github.com/chaqingmodiao/minecraft-mod-analyzer")
        github_label.setStyleSheet("color: #666; font-size: 11px;")
        
        # 分隔符
        separator = QLabel("|")
        separator.setStyleSheet("color: #666; font-size: 11px; padding: 0 5px;")
        
        author_layout.addWidget(author_info)
        author_layout.addWidget(homepage_label)
        author_layout.addWidget(separator)
        author_layout.addWidget(github_label)
        author_layout.addStretch()
        
        control_layout.addLayout(author_layout)
        
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
        self.thread_spin.setValue(5)
        thread_layout.addWidget(self.thread_spin)
        control_layout.addLayout(thread_layout)

        # 生成选项
        options_layout = QHBoxLayout()
        self.gen_log_cb = QCheckBox("生成日志文件")
        self.gen_log_cb.setChecked(True)
        self.gen_folder_cb = QCheckBox("生成分类文件夹")
        self.gen_folder_cb.setChecked(True)
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
        # 表格区域 - 使用QSplitter分割三个区域
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        
        # 创建三个分类的表格
        self.tables = {}
        categories = [
            ("服务端", "green", "适用于服务端的模组"),
            ("仅客户端", "orange", "仅适用于客户端的模组"),
            ("解析失败", "red", "无法解析或读取失败的模组")
        ]
        
        for cat_name, color, description in categories:
            # 创建分组框
            group_box = QGroupBox(cat_name)
            group_box.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {color}; font-size: 14px; }}")
            
            # 创建布局
            group_layout = QVBoxLayout()
            
            # 添加描述标签
            desc_label = QLabel(description)
            desc_label.setStyleSheet(f"color: {color}; font-size: 12px; padding-bottom: 5px;")
            group_layout.addWidget(desc_label)
            
            # 创建表格
            table = QTableWidget()
            table.setColumnCount(4)  # 图标、Mod名称、分类、链接
            table.setHorizontalHeaderLabels(["图标", "Mod 名称", "分类", "链接"])
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # 图标列固定宽度
            table.setColumnWidth(0, 40)  # 减小图标列宽度
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # 名称列自适应
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # 分类列根据内容调整
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # 链接列根据内容调整
            table.setSortingEnabled(True)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.verticalHeader().setDefaultSectionSize(40)  # 减小行高
            table.setAlternatingRowColors(True)  # 交替行颜色
            
            group_layout.addWidget(table)
            group_box.setLayout(group_layout)
            
            self.splitter.addWidget(group_box)
            self.tables[cat_name] = table
        
        # 设置分割器各部分的初始大小
        self.splitter.setSizes([350, 350, 300])
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
        if total>0:
            percent = int(current/total*100)
            self.progress.setFormat(f"[{current}/{total}] {percent}%")
            self.progress.setValue(percent)

    def on_log(self,msg,color):
        if color=="red":
            self.log_text.setTextColor(Qt.GlobalColor.red)
        elif color=="green":
            self.log_text.setTextColor(Qt.GlobalColor.green)
        else:
            self.log_text.setTextColor(Qt.GlobalColor.darkYellow)
        self.log_text.append(msg)
        self.log_text.moveCursor(self.log_text.textCursor().MoveOperation.End)

    def on_mod(self, mod_info):
        table = self.tables.get(mod_info["category"], self.tables["解析失败"])
        row = table.rowCount()
        table.insertRow(row)
        
        # 设置行高
        table.setRowHeight(row, 40)

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

        # 名称列
        name_item = QTableWidgetItem(mod_info["name"])
        # 根据分类设置颜色
        if mod_info["category"] == "服务端":
            name_item.setForeground(QColor("green"))
        elif mod_info["category"] == "仅客户端":
            name_item.setForeground(QColor("orange"))
        else:
            name_item.setForeground(QColor("red"))
        
        table.setItem(row, 1, name_item)

        # 分类列
        category_item = QTableWidgetItem(mod_info["category"])
        # 根据分类设置颜色
        if mod_info["category"] == "服务端":
            category_item.setForeground(QColor("green"))
        elif mod_info["category"] == "仅客户端":
            category_item.setForeground(QColor("orange"))
        else:
            category_item.setForeground(QColor("red"))
        
        table.setItem(row, 2, category_item)

        # 链接列（按钮）
        links_widget = QWidget()
        hbox = QHBoxLayout()
        hbox.setContentsMargins(2, 0, 2, 0)
        
        # 创建链接按钮
        link_buttons = []
        for key, display in zip(["curseforge","modrinth","mcmod"], ["CF","MR","MC百科"]):
            btn = QPushButton(display)
            url = mod_info["links"].get(key)
            if url:
                btn.clicked.connect(lambda checked, url=url: QDesktopServices.openUrl(QUrl(url)))
                btn.setToolTip(url)
            else:
                btn.setEnabled(False)
                btn.setToolTip("链接不可用")
            btn.setMaximumWidth(70)
            btn.setMaximumHeight(28)
            hbox.addWidget(btn)
            link_buttons.append(btn)
        
        # 如果所有链接都不可用，显示提示
        if not any(mod_info["links"].values()):
            no_links_label = QLabel("无链接")
            no_links_label.setStyleSheet("color: gray; font-style: italic;")
            hbox.addWidget(no_links_label)
        
        links_widget.setLayout(hbox)
        table.setCellWidget(row, 3, links_widget)

    def set_output_dir(self, path:Path):
        self.output_dir = path
        self.open_dir_btn.setEnabled(True)
        self.log_text.append(f"\n✅ 分析完成！输出目录: {path}")

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
