"""
Anime Draft - anime seç, rastgele 10 dövüşçü karakter çek (1 tanesi ters köşe), draft'ı kendiniz yapın.
Çalıştırma:  python main.py
"""
import glob
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from openai import OpenAI
from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
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

POWER_LEVELS = {  # anahtar: (yazı, renk)
    "güçlü": ("GÜÇLÜ", "#06d6a0"),
    "orta": ("ORTA GÜÇLÜ", "#ffd166"),
    "güçsüz": ("GÜÇSÜZ", "#ef476f"),
}
PORTRAIT_SIZE = (220, 320)
THUMB_SIZE = (30, 40)


def app_dir():
    # exe olunca dosyalar exe'nin yanına yazılsın
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")
CACHE_PATH = os.path.join(app_dir(), "pools.json")
IMAGE_DIR = os.path.join(app_dir(), "images")


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


def norm(text):
    """Karşılaştırma için sadece harf/rakam bırakır: 'Hunter x Hunter' -> 'hunterxhunter'."""
    return re.sub(r"[\W_]+", "", str(text).lower())


# ---------------------------------------------------------------- AI kısmı
SYSTEM_PROMPT = (
    "Sen anime ve çizgi roman/süper kahraman evrenleri konusunda uzman bir ansiklopedisin. "
    "Sadece gerçek bilgi verirsin, karakter ya da olay uydurmazsın. "
    "İstendiğinde yanıtın her zaman geçerli JSON olur."
)
POWER_RULE = (
    '"power" alanı sadece "güçlü", "orta" ya da "güçsüz" olabilir. Karakteri kendi '
    "serisindeki/evrenindeki diğer dövüşçülerle kıyasla ve ekranda gösterdiği en yüksek seviyeyi "
    "baz al (ters köşelerde o dönemin/versiyonun gücünü). Serinin üst düzey savaşçıları "
    '"güçlü", iyi ama zirvede olmayanlar "orta", alt seviyedekiler "güçsüz" olsun. '
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
        + POWER_RULE
        + '"description" Türkçe, 1-2 cümle olsun: yetenekleri ve dövüş tarzı, büyük spoiler verme; '
        "ters köşelerde neden ters köşe olduğunu da kısaca söyle. Sadece şu formatta JSON döndür: "
        '{"characters": [{"name": "...", "anime": "...", "power": "...", "description": "..."}], '
        '"twists": [{"name": "...", "anime": "...", "power": "...", "description": "...", '
        '"twist_type": "..."}]}'
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
        power = parse_power(c.get("power"))
        if power:
            item["power"] = power
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


def parse_power(value):
    """AI'nın yazdığı güç seviyesini POWER_LEVELS anahtarlarından birine çevirir."""
    v = str(value or "").lower()
    if "orta" in v:
        return "orta"
    if "güçsüz" in v or "zayıf" in v:
        return "güçsüz"
    if "güçlü" in v:
        return "güçlü"
    return None


def rate_power(client, chars):
    """Güç seviyesi olmayan (eski önbellekten gelen) karakterleri puanlatır: {norm(isim): seviye}."""
    lines = "\n".join(f"{i}. {c['name']} — {c['anime']}" for i, c in enumerate(chars, 1))
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                "Aşağıdaki numaralı karakterlerin her birine bir güç seviyesi ver. " + POWER_RULE
                + '"id" alanına karakterin numarasını yaz. Sadece şu formatta JSON döndür: '
                '{"ratings": [{"id": 1, "power": "..."}]}\n\n' + lines
            )},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    ratings = {}
    for r in data.get("ratings", []):
        power = parse_power(r.get("power"))
        try:
            idx = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if power and 1 <= idx <= len(chars):
            ratings[norm(chars[idx - 1]["name"])] = power
    return ratings


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


# ---------------------------------------------------------------- karakter resimleri
USER_AGENT = "AnimeDraft/1.0 (personal hobby app)"
ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query ($search: String) {
  Page(perPage: 8) {
    characters(search: $search, sort: [SEARCH_MATCH, FAVOURITES_DESC]) {
      image { large }
      media(perPage: 10) { nodes { title { romaji english } synonyms } }
    }
  }
}
"""
WIKI_API = "https://en.wikipedia.org/w/api.php"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def http(url, payload=None):
    headers = {"User-Agent": USER_AGENT}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=15) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            wait = e.headers.get("Retry-After", "")  # AniList dakikada 30 istek sınırı koyuyor
            time.sleep(min(int(wait), 60) if wait.isdigit() else 5)


def anilist_search(name):
    data = json.loads(http(ANILIST_URL, {"query": ANILIST_QUERY, "variables": {"search": name}}))
    return data["data"]["Page"]["characters"]


def in_anime(found, anime):
    """AniList'ten gelen karakter bizim karakterin animesinde geçiyor mu?"""
    target = norm(anime)
    for media in found["media"]["nodes"]:
        titles = [media["title"]["romaji"], media["title"]["english"], *(media["synonyms"] or [])]
        for t in map(norm, filter(None, titles)):
            if len(t) >= 4 and (t in target or target in t):
                return True
    return False


def anilist_image(name, anime):
    results = anilist_search(name)
    matches = [c for c in results if in_anime(c, anime)]
    if not matches:
        # İsim AniList'te farklı yazılmış olabilir (Ushoda / Ushouda); parça parça da dene.
        parts = [p for p in sorted(set(name.split()), key=len, reverse=True) if len(p) >= 3]
        for part in parts[:2]:
            if part != name:
                matches = [c for c in anilist_search(part) if in_anime(c, anime)]
                if matches:
                    break
    for c in matches or results[:1]:
        url = c["image"]["large"]
        if url and "default.jpg" not in url:
            return url
    return None


def wiki_image(name):
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": 2,
        "generator": "search", "gsrsearch": f"{name} DC Comics", "gsrlimit": 5,
        "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 400, "pilicense": "any",
    })
    pages = json.loads(http(f"{WIKI_API}?{params}")).get("query", {}).get("pages", [])
    pages = [p for p in sorted(pages, key=lambda p: p["index"]) if p.get("thumbnail")]
    # Arama bazen önce "List of Batman family enemies" gibi sayfalar getiriyor; başlığı isimle başlayanı seç.
    exact = [p for p in pages if norm(p["title"]).startswith(norm(name))]
    best = (exact or pages)[:1]
    return best[0]["thumbnail"]["source"] if best else None


def image_key(ch):
    slug = re.sub(r"\W+", "_", ch["name"].lower()).strip("_")[:40]
    digest = hashlib.md5(f"{ch['anime']}|{ch['name']}".lower().encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}"


def load_image(ch, dc):
    """Karakter resminin dosya yolunu döndürür ("" = bulunamadı). İnternete sadece ilk seferde çıkar."""
    key = image_key(ch)
    for path in glob.glob(os.path.join(IMAGE_DIR, glob.escape(key) + ".*")):
        return "" if path.endswith(".none") else path

    name = re.sub(r"\(.*?\)", "", ch["name"]).strip() or ch["name"]
    url = wiki_image(name) if dc else anilist_image(name, ch["anime"])
    os.makedirs(IMAGE_DIR, exist_ok=True)
    if not url:
        open(os.path.join(IMAGE_DIR, key + ".none"), "w").close()  # bir daha aramasın
        return ""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    path = os.path.join(IMAGE_DIR, key + (ext if ext in IMAGE_EXTS else ".jpg"))
    tmp = os.path.join(IMAGE_DIR, "~" + os.path.basename(path))  # yarım inen dosya önbelleğe girmesin
    with open(tmp, "wb") as f:
        f.write(http(url))
    os.replace(tmp, path)
    return path


class ImageLoader(QObject):
    """Çekilen karakterlerin resimlerini arka planda, çıkış sırasıyla indirir."""
    loaded = Signal(int, int, str)  # tur, karakter sırası, dosya yolu ("" = resim yok)

    def __init__(self):
        super().__init__()
        self.round = 0

    def load(self, chars, dc):
        self.round += 1
        threading.Thread(target=self._run, args=(self.round, list(chars), dc), daemon=True).start()

    def _run(self, rnd, chars, dc):
        for i, ch in enumerate(chars):
            if rnd != self.round:  # yeni çekiliş başladı, bu turu bırak
                return
            try:
                path = load_image(ch, dc)
            except Exception:  # noqa: BLE001 - internet yoksa resimsiz devam
                path = ""
            self.loaded.emit(rnd, i, path)


def show_pixmap(label, pix, loading_text, missing_text):
    """pix: None = hâlâ yükleniyor, boş QPixmap = resim yok."""
    if pix is None:
        label.setText(loading_text)
    elif pix.isNull():
        label.setText(missing_text)
    else:
        dpr = label.devicePixelRatioF()
        scaled = pix.scaled(label.size() * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        label.setPixmap(scaled)


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
        self.chars, self.images, self.thumbs = [], [], []
        self.loader = ImageLoader()
        self.loader.loaded.connect(self.on_image)
        lay = QHBoxLayout(self)

        side = QFrame()
        side.setObjectName("panel")
        s = QVBoxLayout(side)
        header = QLabel("Çıkanlar")
        header.setObjectName("pname")
        self.revealed = QListWidget()
        self.revealed.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.portrait = QLabel()
        self.portrait.setObjectName("portrait")
        self.portrait.setFixedSize(*PORTRAIT_SIZE)
        self.portrait.setAlignment(Qt.AlignCenter)
        self.char_name = QLabel()
        self.char_name.setObjectName("charname")
        self.char_name.setWordWrap(True)
        self.power = QLabel()
        self.power.setObjectName("power")
        self.char_anime = QLabel()
        self.char_desc = QLabel()
        self.char_desc.setObjectName("desc")
        self.char_desc.setWordWrap(True)
        self.char_desc.setAlignment(Qt.AlignLeft | Qt.AlignTop)

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

        info = QVBoxLayout()
        info.addWidget(self.char_name)
        info.addWidget(self.power, 0, Qt.AlignLeft)
        info.addWidget(self.char_anime)
        info.addWidget(self.char_desc, 1)
        body = QHBoxLayout()
        body.setSpacing(20)
        body.addWidget(self.portrait, 0, Qt.AlignTop)
        body.addLayout(info, 1)

        c.addWidget(self.counter)
        c.addWidget(self.badge)
        c.addLayout(body, 1)
        c.addWidget(self.next_btn)
        c.addLayout(row)

        lay.addWidget(side, 2)
        lay.addWidget(card, 5)

    def start(self, chars, dc=False):
        self.chars = chars
        self.images = [None] * len(chars)  # None = yükleniyor
        self.thumbs = []
        self.shown = 0
        self.revealed.clear()
        self.loader.load(chars, dc)
        self.reveal_next()

    def reveal_next(self):
        if self.shown >= len(self.chars):
            return
        self.shown += 1
        self.add_row(self.shown - 1)
        self.revealed.setCurrentRow(self.shown - 1)
        more = self.shown < len(self.chars)
        self.next_btn.setEnabled(more)
        self.next_btn.setText("Sonraki karakter ➜" if more else "Hepsi çıktı")

    def add_row(self, i):
        ch = self.chars[i]
        row = QWidget()
        row.setObjectName("row")
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        thumb = QLabel()
        thumb.setObjectName("thumb")
        thumb.setFixedSize(*THUMB_SIZE)
        thumb.setAlignment(Qt.AlignCenter)
        text = QVBoxLayout()
        text.setSpacing(0)
        prefix = "🔄 " if "twist" in ch else ""
        name = QLabel(f"{i + 1}. {prefix}{ch['name']}")
        name.setMinimumWidth(1)  # uzun isim listeyi yana kaydırmasın, kesilsin
        name.setToolTip(ch["name"])
        text.addWidget(name)
        level = POWER_LEVELS.get(ch.get("power"))
        if level:
            power = QLabel(level[0])
            power.setObjectName("rowpower")
            power.setStyleSheet(f"color: {level[1]};")
            text.addWidget(power)
        h.addWidget(thumb)
        h.addLayout(text, 1)

        item = QListWidgetItem()
        item.setSizeHint(QSize(0, row.sizeHint().height()))
        self.revealed.addItem(item)
        self.revealed.setItemWidget(item, row)
        self.thumbs.append(thumb)
        show_pixmap(thumb, self.images[i], "", "?")

    def on_image(self, rnd, i, path):
        if rnd != self.loader.round:  # eski çekilişten kalan resim
            return
        self.images[i] = QPixmap(path) if path else QPixmap()
        if i < len(self.thumbs):
            show_pixmap(self.thumbs[i], self.images[i], "", "?")
        if self.revealed.currentRow() == i:
            show_pixmap(self.portrait, self.images[i], "Resim yükleniyor...", "Resim bulunamadı")

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
        level = POWER_LEVELS.get(ch.get("power"))
        if level:
            self.power.setText(level[0])
            self.power.setStyleSheet(f"background: {level[1]};")
            self.power.show()
        else:
            self.power.hide()
        self.char_anime.setText(ch["anime"])
        self.char_desc.setText(ch["description"])
        show_pixmap(self.portrait, self.images[row], "Resim yükleniyor...", "Resim bulunamadı")


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
        fetch = not entry or needs_more(entry)
        # eski sürümde çekilen karakterlerin güç seviyesi yok, onları bir kerelik puanlat
        unrated = [c for c in entry["pool"] if "power" not in c] if entry else []

        if not fetch and not unrated:
            self.show_chars(entry)
            return

        if not self.ensure_client():
            if entry and split_pool(entry)[0]:  # key yoksa eldekiyle oynat
                self.show_chars(entry)
            return

        client = self.client
        known = [c["name"] for c in entry["pool"]] if entry else []
        if not entry:
            self.loading.label.setText(f"{mode} için dövüşçü havuzu hazırlanıyor...")
            entry = {"pool": [], "used": []}
        elif fetch:
            self.loading.label.setText(f"{mode} için yeni karakterler aranıyor...")
        else:
            self.loading.label.setText(f"{mode} karakterlerinin güç seviyeleri belirleniyor...")

        def job():
            new_chars = fetch_pool(client, mode, exclude=known) if fetch else []
            try:
                ratings = rate_power(client, unrated) if unrated else {}
            except Exception:  # noqa: BLE001 - güç seviyesi olmadan da oynanabilir
                ratings = {}
            return new_chars, ratings

        self.pending_entry = entry
        self.stack.setCurrentWidget(self.loading)
        self.worker = Worker(job)
        self.worker.done.connect(self.on_pool_ready)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def on_pool_ready(self, result):
        new_chars, ratings = result
        entry = self.pending_entry
        for c in entry["pool"]:
            power = ratings.get(norm(c["name"]))
            if power:
                c["power"] = power
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
        self.reveal.start(chars, dc=self.anime == DC_MODE)
        self.stack.setCurrentWidget(self.reveal)


STYLE = """
QWidget { background: #14151a; color: #e8e8ee; font-family: Segoe UI, Arial; font-size: 14px; }
QLabel { background: transparent; }
QLabel#title { font-size: 34px; font-weight: 800; color: #ff5c5c; padding: 16px; }
QLabel#charname { font-size: 34px; font-weight: 800; color: #ffd166; padding-top: 12px; }
QLabel#badge { font-size: 18px; font-weight: 800; color: #14151a; background: #ff9f1c;
               border-radius: 8px; padding: 6px 14px; }
QLabel#desc { font-size: 17px; padding: 12px 0; }
QLabel#portrait { background: #14151a; border: 1px solid #2c2f3a; border-radius: 10px; color: #5a5d68; }
QLabel#power { color: #14151a; font-size: 14px; font-weight: 800; border-radius: 6px; padding: 4px 12px; }
QListWidget::item { border-radius: 6px; }
QListWidget::item:selected { background: #3a3e4d; }
QWidget#row { background: transparent; }
QLabel#rowpower { font-size: 11px; font-weight: 800; }
QLabel#thumb { background: #2c2f3a; border-radius: 4px; color: #5a5d68; }
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