"""
Anime Draft - anime seç, rastgele 10 dövüşçü karakter çek (1 tanesi ters köşe), draft'ı kendiniz yapın.
Çalıştırma:  python main.py
"""
import json
import os
import random
import sys

from openai import OpenAI
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

# ---------------------------------------------------------------- ayarlar
MODEL = "gpt-4.1-mini"          # istersen değiştir
CHARS_PER_GAME = 10             # bir çekilişteki toplam karakter
TWISTS_PER_GAME = 1             # bunların kaçı ters köşe
POOL_SIZE = 30                  # AI'dan istenen normal karakter havuzu
TWIST_POOL_SIZE = 8             # AI'dan istenen ters köşe havuzu
NORMALS_PER_GAME = CHARS_PER_GAME - TWISTS_PER_GAME

ANIME_LIST = [
    "Bleach", "One Piece", "Naruto", "Boku no Hero Academia", "Fairy Tail",
    "Fullmetal Alchemist: Brotherhood", "Hellsing", "Hunter x Hunter",
    "Jujutsu Kaisen", "Kimetsu no Yaiba", "Nanatsu no Taizai", "One Punch Man",
    "Ousama Ranking", "Saiki Kusuo no Ψ-nan", "Shingeki no Kyojin",
    "Shinseiki Evangelion (Neon Genesis Evangelion)", "Tengen Toppa Gurren Lagann",
    "Vinland Saga",
]
CHARISMA_MODE = "⭐ Karizmatik Karakterler (karışık)"
DC_MODE = "🦇 DC Kahramanları"
ALL_MODES = ANIME_LIST + [CHARISMA_MODE, DC_MODE]


def app_dir():
    # exe olunca dosyalar exe'nin yanına yazılsın
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")
CACHE_PATH = os.path.join(app_dir(), "pools.json")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_api_key():
    return os.environ.get("OPENAI_API_KEY") or load_json(CONFIG_PATH, {}).get("openai_api_key")


# ---------------------------------------------------------------- AI kısmı
SYSTEM_PROMPT = (
    "Sen anime ve çizgi roman/süper kahraman evrenleri konusunda uzman bir ansiklopedisin. "
    "Sadece gerçek bilgi verirsin, karakter ya da olay uydurmazsın. "
    "İstendiğinde yanıtın her zaman geçerli JSON olur."
)


def pool_prompt(mode, exclude=()):
    if mode == DC_MODE:
        intro = (f"DC evreninden {POOL_SIZE} kahraman listele; Batman, Superman gibi ana "
                 "kadrodan az bilinenlere kadar karışık olsun. ")
        screen = "film, dizi ya da animasyon yapımlarında"
        source = '"anime" alanına karakterin dövüştüğü bilinen bir yapımın adını yaz. '
        twist_scope = "DC evreninden"
    elif mode == CHARISMA_MODE:
        intro = (f"Tüm zamanların en popüler animelerinden, farklı serilerden karışık olacak "
                 f"şekilde en karizmatik ve en sevilen {POOL_SIZE} karakteri listele. ")
        screen = "animede"
        source = '"anime" alanına karakterin animesini yaz. '
        twist_scope = "popüler animelerden"
    else:
        intro = (f"'{mode}' animesinden {POOL_SIZE} farklı karakter listele. Ana karakterlerle "
                 "az bilinen dövüşçüleri karışık ver, sadece en ünlüleri sayma. Kurallara uyan "
                 "bu kadar karakter yoksa uyanların hepsini ver, sayıyı doldurmak için kuralı "
                 "çiğneme. ")
        screen = "animede (sadece mangada değil)"
        source = f'"anime" alanına "{mode}" yaz (ters köşelerde hangi dönemden olduğunu da belirt). '
        twist_scope = "aynı seriden ya da aynı serinin farklı bir döneminden/devamından"

    rules = (
        f"KURALLAR: Her karakterin {screen} ekranda en az bir kez dövüştüğü gösterilmiş olmalı, "
        "kısa bir sahne de olsa olur. Yan karakterler olabilir, gücü tam bilinmese de olur ama "
        "dövüşte bir işlevi olmalı. Hiç dövüşmeyen, sadece izleyen, komedi ya da destek amaçlı "
        "karakterleri KESİNLİKLE alma. "
    )
    twist = (
        f'Ayrıca ayrı bir "twists" listesine {twist_scope} {TWIST_POOL_SIZE} tane TERS KÖŞE '
        "karakter ekle. Ters köşe, draftı şaşırtacak biri olmalı ve şu türlerden birine uymalı: "
        '"Farklı dönem" (örneğin Naruto Shippuden draftına klasik Naruto döneminden güçlü biri '
        'ya da serinin başka bir versiyonundan biri), "OP" (dengeyi bozacak kadar güçlü biri), '
        '"Leş" (çok zayıf, işe yaramaz görünen biri) veya "Nefret edilen" (fanların sevmediği, '
        "sinir bozucu biri). Türleri karışık dağıt. Ters köşeler de dövüşme kuralına uymak "
        "zorunda ve normal listedekilerle aynı kişi olmamalı. "
    )
    fmt = (
        source
        + '"description" Türkçe, 1-2 cümle olsun: yetenekleri ve dövüş tarzı, büyük spoiler verme; '
        "ters köşelerde neden ters köşe olduğunu da kısaca söyle. Sadece şu formatta JSON döndür: "
        '{"characters": [{"name": "...", "anime": "...", "description": "..."}], '
        '"twists": [{"name": "...", "anime": "...", "description": "...", "twist_type": "..."}]}'
    )
    avoid = ""
    if exclude:
        avoid = ("Şu karakterler zaten elimizde, hiçbir listede TEKRAR VERME, sadece yenilerini "
                 "ver: " + ", ".join(exclude) + ". ")
    return intro + rules + twist + avoid + fmt


def fetch_pool(client, mode, exclude=()):
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0.9,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": pool_prompt(mode, exclude)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    pool, seen = [], {n.lower() for n in exclude}
    fixed_source = mode not in (CHARISMA_MODE, DC_MODE)

    def add(c, twist_type=None):
        name = str(c.get("name", "")).strip()
        if not name or name.lower() in seen:
            return
        seen.add(name.lower())
        src = str(c.get("anime", "")).strip()
        item = {
            "name": name,
            "anime": src or mode if (twist_type or not fixed_source) else mode,
            "description": str(c.get("description", "")).strip(),
        }
        if twist_type:
            item["twist"] = twist_type
        pool.append(item)

    for c in data.get("characters", []):
        add(c)
    for c in data.get("twists", []):
        add(c, str(c.get("twist_type") or "Sürpriz").strip())

    if not exclude and sum(1 for c in pool if "twist" not in c) < NORMALS_PER_GAME:
        raise RuntimeError(f"AI bu seçim için yeterli dövüşen karakter bulamadı ({len(pool)} tane).")
    return pool


def split_pool(entry):
    normals = [c for c in entry["pool"] if "twist" not in c]
    twists = [c for c in entry["pool"] if "twist" in c]
    return normals, twists


def count_unused(group, used):
    return sum(1 for c in group if c["name"].lower() not in used)


def needs_more(entry):
    used = {n.lower() for n in entry["used"]}
    normals, twists = split_pool(entry)
    return count_unused(normals, used) < NORMALS_PER_GAME or count_unused(twists, used) < TWISTS_PER_GAME


def pick_group(group, used, k):
    """Önce çıkmamışlardan çeker; yetmezse eksiği eskilerden tamamlar ve o grubu sıfırlar."""
    k = min(k, len(group))
    unused = [c for c in group if c["name"].lower() not in used]
    if len(unused) >= k:
        return random.sample(unused, k), False
    rest = [c for c in group if c["name"].lower() in used]
    return unused + random.sample(rest, k - len(unused)), True


def pick_chars(entry):
    used = {n.lower() for n in entry["used"]}
    normals, twists = split_pool(entry)
    n_twists = TWISTS_PER_GAME if twists else 0
    chosen_n, reset_n = pick_group(normals, used, CHARS_PER_GAME - n_twists)
    chosen_t, reset_t = pick_group(twists, used, n_twists)

    if reset_n:
        used -= {c["name"].lower() for c in normals}
    if reset_t:
        used -= {c["name"].lower() for c in twists}
    chosen = chosen_n + chosen_t
    used |= {c["name"].lower() for c in chosen}
    entry["used"] = sorted(used)

    random.shuffle(chosen)  # ters köşe de rastgele bir sırada çıkar
    return chosen


def get_entry(cache, mode):
    """Her mod için {"pool": [...], "used": [...]} tutulur. Eski formatları da okur."""
    entry = cache.get(mode)
    if isinstance(entry, list):
        entry = {"pool": entry, "used": []}
    return entry


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


# ---------------------------------------------------------------- sayfalar
class SetupPage(QWidget):
    start_requested = Signal(str, bool)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)

        title = QLabel("ANIME DRAFT")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        self.anime = QComboBox()
        self.anime.addItems(ALL_MODES)
        self.anime.setMaxVisibleItems(20)
        self.refresh = QCheckBox("Havuzu sıfırdan oluştur (API'yi tekrar çağırır)")
        lay.addWidget(QLabel("Anime seç"))
        lay.addWidget(self.anime)
        lay.addWidget(self.refresh)

        btn = QPushButton(f"🎲 {CHARS_PER_GAME} karakter çek")
        btn.setObjectName("big")
        btn.clicked.connect(
            lambda: self.start_requested.emit(self.anime.currentText(), self.refresh.isChecked())
        )
        lay.addWidget(btn)


class LoadingPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        self.label = QLabel("Karakter havuzu hazırlanıyor...")
        self.label.setAlignment(Qt.AlignCenter)
        bar = QProgressBar()
        bar.setRange(0, 0)
        lay.addWidget(self.label)
        lay.addWidget(bar)


class RevealPage(QWidget):
    back = Signal()
    reroll = Signal()

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)

        side = QFrame()
        side.setObjectName("panel")
        s = QVBoxLayout(side)
        header = QLabel("Çıkanlar")
        header.setObjectName("pname")
        self.revealed = QListWidget()
        self.revealed.currentRowChanged.connect(self.show_char)
        s.addWidget(header)
        s.addWidget(self.revealed)

        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        self.counter = QLabel()
        self.counter.setAlignment(Qt.AlignCenter)
        self.badge = QLabel()
        self.badge.setObjectName("badge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.char_name = QLabel()
        self.char_name.setObjectName("charname")
        self.char_name.setAlignment(Qt.AlignCenter)
        self.char_name.setWordWrap(True)
        self.char_anime = QLabel()
        self.char_anime.setAlignment(Qt.AlignCenter)
        self.char_desc = QLabel()
        self.char_desc.setObjectName("desc")
        self.char_desc.setWordWrap(True)
        self.char_desc.setAlignment(Qt.AlignCenter)

        self.next_btn = QPushButton("Sonraki karakter ➜")
        self.next_btn.setObjectName("big")
        self.next_btn.clicked.connect(self.reveal_next)

        row = QHBoxLayout()
        home = QPushButton("⟵ Anime seçimine dön")
        home.clicked.connect(self.back)
        again = QPushButton("🎲 Aynı animeden yeniden çek")
        again.clicked.connect(self.reroll)
        row.addWidget(home)
        row.addWidget(again)

        c.addWidget(self.counter)
        c.addWidget(self.badge)
        c.addWidget(self.char_name)
        c.addWidget(self.char_anime)
        c.addWidget(self.char_desc, 1)
        c.addWidget(self.next_btn)
        c.addLayout(row)

        lay.addWidget(side, 1)
        lay.addWidget(card, 3)

    def start(self, chars):
        self.chars = chars
        self.shown = 0
        self.revealed.clear()
        self.reveal_next()

    def reveal_next(self):
        if self.shown >= len(self.chars):
            return
        ch = self.chars[self.shown]
        self.shown += 1
        prefix = "🔄 " if "twist" in ch else ""
        self.revealed.addItem(f"{self.shown}. {prefix}{ch['name']}")
        self.revealed.setCurrentRow(self.shown - 1)
        more = self.shown < len(self.chars)
        self.next_btn.setEnabled(more)
        self.next_btn.setText("Sonraki karakter ➜" if more else "Hepsi çıktı")

    def show_char(self, row):
        if row < 0:
            return
        ch = self.chars[row]
        self.counter.setText(f"Karakter {row + 1} / {len(self.chars)}")
        if "twist" in ch:
            self.badge.setText(f"🔄 TERS KÖŞE — {ch['twist']}")
            self.badge.show()
        else:
            self.badge.hide()
        self.char_name.setText(ch["name"])
        self.char_anime.setText(ch["anime"])
        self.char_desc.setText(ch["description"])


# ---------------------------------------------------------------- ana pencere
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Anime Draft")
        self.resize(1000, 640)
        self.client = None
        self.worker = None
        self.anime = None

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.setup = SetupPage()
        self.loading = LoadingPage()
        self.reveal = RevealPage()
        for page in (self.setup, self.loading, self.reveal):
            self.stack.addWidget(page)

        self.setup.start_requested.connect(self.start_game)
        self.reveal.back.connect(lambda: self.stack.setCurrentWidget(self.setup))
        self.reveal.reroll.connect(lambda: self.start_game(self.anime, False))

    def ensure_client(self):
        if self.client:
            return True
        key = get_api_key()
        if not key:
            key, ok = QInputDialog.getText(
                self, "OpenAI API key", "API key'ini yapıştır (config.json'a kaydedilecek):",
                QLineEdit.Password,
            )
            key = key.strip()
            if not ok or not key:
                return False
            save_json(CONFIG_PATH, {"openai_api_key": key})
        self.client = OpenAI(api_key=key)
        return True

    def start_game(self, mode, refresh):
        self.anime = mode
        entry = None if refresh else get_entry(load_json(CACHE_PATH, {}), mode)

        if entry and not needs_more(entry):
            self.show_chars(entry)
            return

        if not self.ensure_client():
            if entry and split_pool(entry)[0]:  # key yoksa eldekiyle oynat
                self.show_chars(entry)
            return

        if entry:
            self.loading.label.setText(f"{mode} için yeni karakterler aranıyor...")
            known = [c["name"] for c in entry["pool"]]
            fn = lambda: fetch_pool(self.client, mode, exclude=known)
        else:
            self.loading.label.setText(f"{mode} için dövüşçü havuzu hazırlanıyor...")
            entry = {"pool": [], "used": []}
            fn = lambda: fetch_pool(self.client, mode)

        self.pending_entry = entry
        self.stack.setCurrentWidget(self.loading)
        self.worker = Worker(fn)
        self.worker.done.connect(self.on_pool_ready)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def on_pool_ready(self, new_chars):
        entry = self.pending_entry
        entry["pool"] += new_chars
        self.show_chars(entry)

    def on_error(self, msg):
        QMessageBox.critical(self, "Hata", f"API tarafında bir sorun çıktı:\n\n{msg}")
        self.stack.setCurrentWidget(self.setup)

    def show_chars(self, entry):
        chars = pick_chars(entry)
        cache = load_json(CACHE_PATH, {})
        cache[self.anime] = entry
        save_json(CACHE_PATH, cache)
        self.reveal.start(chars)
        self.stack.setCurrentWidget(self.reveal)


STYLE = """
QWidget { background: #14151a; color: #e8e8ee; font-family: Segoe UI, Arial; font-size: 14px; }
QLabel#title { font-size: 34px; font-weight: 800; color: #ff5c5c; padding: 16px; }
QLabel#charname { font-size: 34px; font-weight: 800; color: #ffd166; padding-top: 12px; }
QLabel#badge { font-size: 18px; font-weight: 800; color: #14151a; background: #ff9f1c;
               border-radius: 8px; padding: 6px 14px; }
QLabel#desc { font-size: 17px; padding: 20px; }
QLabel#pname { font-size: 20px; font-weight: 700; }
QFrame#panel, QFrame#card { background: #1d1f27; border: 1px solid #2c2f3a; border-radius: 12px; }
QPushButton { background: #2c2f3a; border: none; border-radius: 8px; padding: 10px 14px; font-weight: 600; }
QPushButton:hover { background: #3a3e4d; }
QPushButton:disabled { color: #5a5d68; background: #202229; }
QPushButton#big { background: #ff5c5c; color: white; font-size: 18px; padding: 14px; }
QPushButton#big:hover { background: #ff7676; }
QPushButton#big:disabled { background: #4a2a2a; color: #888; }
QComboBox, QListWidget { background: #1d1f27; border: 1px solid #2c2f3a; border-radius: 8px; padding: 6px; }
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()