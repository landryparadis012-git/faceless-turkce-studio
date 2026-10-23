"""Faceless Türkçe Stüdyo v8 — Groq + Gemini script · Cloudflare images · edge-tts Turkish · ffmpeg edit.

Differences from v7 (faceless-studio):
  * Everything is TURKISH: script, narration, title, description, hashtags.
  * NO TEXT IS BURNED INTO THE FRAME (no captions, no "Bölüm" label). The
    platform generates subtitles at upload time, so the frames are language-free.
  * 7 levels x 3 shots = 21 images (v7: 14).
  * Shot 2 of every level is a first-person POV; time jumps between levels.
  * Every episode is written with a different "flavor", and flavors rotate in order.
  * Series memory (memory/series_state.json) + automatic wait when the daily quota runs out.

NOTE FOR THE OWNER: all code comments and log lines are in English on purpose.
Only the strings sent to the LLM (ROLE / SCHEMA / prompts) are Turkish, because
those are what make the generated script Turkish. Changing them changes the output.
"""
import argparse, base64, datetime, json, os, socket, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
import requests

import hub

socket.setdefaulttimeout(120)
ROOT = Path(__file__).parent
EPS = ROOT / "episodes"

VOICE = os.environ.get("VOICE", "tr-TR-AhmetNeural")   # tr-TR-EmelNeural = female
FPS = 30
GROQ = "https://api.groq.com/openai/v1/chat/completions"
GROQ_UA = "faceless-turkce-studio/1.0"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
MAX_EPISODES = 8          # sync-state.yml reads this line with a regex — keep the shape
LEVEL_COUNT = 7
SHOTS_PER_LEVEL = 3
TOTAL_SHOTS = LEVEL_COUNT * SHOTS_PER_LEVEL            # 21
MAX_OUT_TOKENS = 9000     # 21 çekim + gpt-oss düşünme payı (v7: 6000/14 çekim)
FLAVORS = ["hayatta kalma", "ihanet", "yükseliş", "intikam", "gizem", "kader", "fedakârlık"]

# Every NEW series draws the next seed here, so consecutive series stop looking
# like reruns of the same farmer. Inside one series the character is frozen in
# memory/series_state.json, so the visuals stay consistent episode to episode.
SERIES_SEEDS = [
    ("12. yüzyıl Anadolu'sunda bir keresteci köyü", "toprak ağası ile köylü arasında kalan bir aile"),
    ("1920'ler İstanbul limanı", "bir hamalın kaçakçılar ve gümrük arasında seçim yapması"),
    ("1960'larda Almanya'ya göçen bir aile", "memlekette bırakılan çocuk ve geri dönüş sözü"),
    ("Antik Roma'da bir gladyatör okulu", "özgürlüğünü satın almak için dövüşen bir köle"),
    ("17. yüzyıl Osmanlı saray mutfağı", "sarayda zehir iddiası ve bir aşçının sırrı"),
    ("kuzeyde bir Viking balıkçı köyü", "kan davası yüzünden sürgün edilen bir gemi ustası"),
    ("Brezilya kahve tarlaları, 1900'ler", "borç sözleşmesiyle toprağa bağlanan bir işçi ailesi"),
    ("Sibirya'da bir avcı kulübesi", "kışın ortasında kaybolan kardeşi arayan bir kadın"),
    ("Mısır'da bir piramit şantiyesi", "ustabaşının oğlu ve yıllardır saklanan bir plan"),
    ("1990'lar İstanbul'unda bir gecekondu mahallesi", "arsa tapusu yüzünden bölünen bir aile"),
    ("İpek Yolu'nda bir kervan", "yol kesicilerle pazarlık eden genç bir tüccar"),
    ("bir maden kasabası, 1930'lar", "göçükten sonra kurtarma ekibine katılan bir madenci"),
]


def _used_seeds():
    f = ROOT / "memory" / "used_seeds.json"
    try:
        used = json.loads(f.read_text())
        return used if isinstance(used, list) else []
    except Exception:
        return []


def _next_seed():
    used = _used_seeds()
    idx = len(used) % len(SERIES_SEEDS)
    era, conflict = SERIES_SEEDS[idx]
    used.append(idx)
    (ROOT / "memory").mkdir(exist_ok=True)
    (ROOT / "memory" / "used_seeds.json").write_text(json.dumps(used))
    return idx, era, conflict


def series_len():
    """OpsHub may ask for a shorter/longer arc; falls back to MAX_EPISODES."""
    return hub.series_length(MAX_EPISODES)

class DailyLimitError(Exception):
    pass

def sh(cmd):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if p.returncode != 0:
        print(f"[cmd failed] {p.stderr[-2000:]}")
        raise RuntimeError(f"command failed: {cmd[0]}")

def dur(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                   "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.decode().strip())

# The image model only understands English, so every visual prompt stays English.
# The character string is repeated word-for-word in EVERY scene so the face never drifts.
DEFAULT_BIBLE = {
    "character": "a 45-year-old farmer man, weathered olive-tan skin, kind deep-set brown eyes, short black hair with gray at the temples, thick eyebrows, a small scar over his left eyebrow, wearing a patched beige linen tunic and a worn copper amulet on a leather cord, mud-flecked leather boots",
    "character_pov": "first-person POV shot of the same man, face never visible: his own weathered olive-tan hands with dirt under the nails in the foreground, sleeves of a patched beige linen tunic, worn copper amulet on a leather cord swinging at the bottom of frame, mud-flecked leather boots below, camera is his eyes",
    "style": "cinematic photorealistic period drama still, shot on 85mm lens, shallow depth of field, volumetric golden-hour light with soft god rays, rich film grain, deep teal and amber color grade, hyper-detailed skin and fabric textures, dramatic composition, prestige TV quality, vertical 9:16 framing, absolutely no text, no letters, no words, no signs, no watermarks",
    "negative": "text, letters, words, subtitles, captions, signage, watermark, logo, deformed hands, extra limbs, blurry, cartoonish, plastic skin",
    "seed": 777123,
}

# ---------------------------------------------------------------- prompts (Turkish on purpose)
# Kept as separate pieces and concatenated instead of .format(), so JSON curly
# braces can't collide with format fields (that was the source of v7's crashes).
ROLE = (
    "Sen 'her seviyede hayat' tarzında dikey video dizileri yazan kıdemli bir senaristsin.\n"
    "Yazdığın bölümler sinematik, duygusal ve izlenme süresini maksimize edecek şekilde kurgulanır.\n"
    "DİL: Her şey TÜRKÇE olacak — anlatım, başlık, açıklama, hashtag. Doğal, akıcı, bir hikâye\n"
    "anlatıcısının sesiyle yaz. Çeviri kokan cümle kurma, İngilizce tek bir kelime bile kullanma.\n"
    "Sadece 'visual' alanları İngilizce yazılır (görsel model İngilizce anlıyor).\n"
)

STRUCTURE = (
    "YAPI: 7 seviye (Level 1, 2, 5, 10, 25, 50, 100). Her seviyede TAM 3 çekim:\n"
    "  A) VARIŞ    -> geniş açı, yeni ortama giriş            (mode: \"wide\")\n"
    "  B) KARMAŞA  -> birinci şahıs POV, kamera karakterin gözü (mode: \"pov\")\n"
    "  C) SONUÇ    -> yakın plan: kazanç, şok ya da ders       (mode: \"closeup\")\n"
    f"Toplam TAM {TOTAL_SHOTS} çekim. Her seviye kendi mini hikâyesidir: gelir, sorun çıkar,\n"
    "çözülür ya da ters köşe olur. Ortam giderek yükselir: köy / kulübe -> şehir -> saray.\n\n"
    "ZAMAN ATLAMASI: Yeni bir seviyeye geçerken zaman atlaması yap ve bunu anlatımda\n"
    "AÇIKÇA söyle: \"Üç yıl sonra...\", \"Kış geldiğinde...\", \"O gece...\", \"Sabah olduğunda...\".\n"
    "Zaman atlaması olmadan seviye atlama; 'time' alanına da o ifadeyi yaz.\n"
)

def visual_rules(bible, fresh=False):
    if fresh:
        cast = (
            "  - mode wide/closeup ise, memory içinde döndürdüğün \"character\" dizesini\n"
            "    HER görselde kelimesi kelimesine tekrar et (yüz hiç değişmesin).\n"
            "  - mode pov ise \"character_pov\" dizesini tekrar et (yüz görünmez, kamera onun gözü).\n"
        )
    else:
        cast = (
            "  - mode wide/closeup ise KARAKTER dizesinin TAMAMI kelimesi kelimesine (yüz hiç değişmesin):\n"
            f"    {bible['character']}\n"
            "  - mode pov ise POV KARAKTER dizesi (yüz görünmez, kamera onun gözü):\n"
            f"    {bible['character_pov']}\n"
        )
    return (
        "GÖRSEL: her çekimin 'visual' alanı İNGİLİZCE yazılır ve şunları içerir:\n"
        + cast +
        "  - yüzdeki duygu + net bir fiziksel eylem + en az 3 somut ortam detayı (eşya, hava, kalabalık, doku)\n"
        "  - kamera dili: varışta geniş açı, güç anında alçak açı, yüz yüze hesaplaşmada omuz üstü,\n"
        "    duygusal dorukta ekstrem yakın plan\n"
        "  - METİN YASAK: hiçbir görselde yazı, harf, tabela, afiş, altyazı olmasın.\n"
        "  - stil ekini SEN EKLEME; kod ekliyor.\n"
    )

NARRATION_RULES = (
    "ANLATIM: her çekim 15-21 kelime, tek parça; 21 çekim boyunca TEK akıcı hikâye.\n"
    "1. çekim 3 saniyelik kanca ile başlar (çarpıcı bir cümle ya da soru), asla yavaş giriş yapmaz.\n"
    "Kısa cümleler kur, sesli okunacağını unutma; ekranda altyazı OLMAYACAK, o yüzden\n"
    "anlatım tek başına anlaşılır olmalı.\n"
)

ENDING_RULES = (
    "ETKİLEŞİM BİTİŞİ (zorunlu): son çekimin anlatımı şu üç kalıptan BİRİYLE biter ve\n"
    "her bölümde farklı bir kalıp kullanılır:\n"
    "  - izleyiciye sorulan cliffhanger sorusu (\"Kral sonunda kabul edecek mi?\")\n"
    "  - sonraki bölüm sözü (\"Yarın 5. bölüm — kaçırma, takip et.\")\n"
    "  - yorum tuzağı (\"Onu bıraksaydın yorumlara AFFET yaz.\")\n"
)

SCHEMA = """
SADECE geçerli JSON döndür (markdown yok, açıklama yok, JSON'dan önce/sonra tek karakter yok):
{
  "title": "<dizi adı + bölüm numarası, en fazla 60 karakter>",
  "hook": "<açılış anlatım satırı, en fazla 15 kelime>",
  "flavor": "<bu bölümün tadı>",
  "caption": "<yükleme açıklaması, en fazla 150 karakter: çarpıcı bir cümle ya da soru + bu bölümün numarası + takip çağrısı>",
  "hashtags": ["#hikaye", "#kesfet", "#fyp", "<dizi adı tek kelime olarak #ile>", "<hikâyeye uygun bir etiket>"],
  "shots": [
    {"level": 1, "beat": "varış",   "mode": "wide",    "time": "",                "narration": "<15-21 kelime>", "visual": "<İngilizce, kurallara uygun>"},
    {"level": 1, "beat": "karmaşa", "mode": "pov",     "time": "",                "narration": "<15-21 kelime>", "visual": "<İngilizce, POV kurallarına uygun>"},
    {"level": 1, "beat": "sonuç",   "mode": "closeup", "time": "",                "narration": "<15-21 kelime>", "visual": "<İngilizce, kurallara uygun>"},
    {"level": 2, "beat": "varış",   "mode": "wide",    "time": "Üç yıl sonra",    "narration": "<15-21 kelime>", "visual": "..."}
    ... TAM 21 çekim, son çekim etkileşim kalıbıyla biter ...
  ],
  "memory": {
    "series_title": "<dizi adı>",
    "this_episode_recap": "<3-4 cümle: BU bölümde ne oldu>",
    "ending_cliffhanger": "<sonraki bölümün çözeceği tam cliffhanger>",
    "ending_pattern_used": "<cliffhanger sorusu | sonraki bölüm sözü | yorum tuzağı>",
    "flavor": "<bu bölümün tadı>"
  }
}
KURALLAR: gerçek marka ya da gerçek kişi yok; görseller hep AYNI kişiyi anlatır;
her çekimde mode alanı yukarıdaki sıraya uyar (wide, pov, closeup); 21 çekimden az ya da çok yazma.
"""

def build_prompt(memory_text, bible, flavor, last_flavor, fresh=False):
    avoid = (f"Önceki bölümün tadı \"{last_flavor}\" idi; aynısını TEKRAR ETME.\n"
             if last_flavor else "")
    return (
        ROLE + "\n"
        + memory_text + "\n\n"
        + STRUCTURE + "\n"
        + visual_rules(bible, fresh) + "\n"
        + NARRATION_RULES + "\n"
        + f"BÖLÜM TADI: bu bölümün tadı \"{flavor}\" olacak. {avoid}"
          "Tadı hikâyenin temposuna, gerilimine ve sonucuna yansıt.\n\n"
        + ENDING_RULES + "\n"
        + SCHEMA
    )

# ---------------------------------------------------------------- LLM layer
def _extract_json(txt):
    txt = txt.strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i == -1 or j == -1:
        raise ValueError(f"no JSON object found in: {txt[:200]}")
    return json.loads(txt[i:j + 1])

def _groq_call_curl(model, prompt, key):
    payload = json.dumps({"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "response_format": {"type": "json_object"},
                          "max_completion_tokens": MAX_OUT_TOKENS,
                          "temperature": 1.0})
    p = subprocess.run(["curl", "-s", "-w", "\n%{http_code}", "--max-time", "120",
                        "-X", "POST", GROQ,
                        "-H", f"Authorization: Bearer {key}",
                        "-H", "Content-Type: application/json",
                        "-d", payload],
                       capture_output=True, text=True, timeout=150)
    if p.returncode != 0:
        raise RuntimeError(f"curl transport error: {p.stderr[:150]}")
    body, _, code = p.stdout.rpartition("\n")
    if code.strip() != "200":
        raise RuntimeError(f"curl HTTP {code.strip()}: {body[:150]}")
    out = json.loads(body)
    return _extract_json(out["choices"][0]["message"]["content"])

def groq_json(prompt):
    key = os.environ["GROQ_API_KEY"]
    last = None
    for model in GROQ_MODELS:
        blocked = False
        for attempt in range(1, 4):
            try:
                print(f"[groq] trying {model} (attempt {attempt}/3)")
                body = {"model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "max_completion_tokens": MAX_OUT_TOKENS,
                        "temperature": 1.0}
                req = urllib.request.Request(
                    GROQ, data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}",
                             "User-Agent": GROQ_UA})
                out = json.loads(urllib.request.urlopen(req, timeout=120).read())
                result = _extract_json(out["choices"][0]["message"]["content"])
                print(f"[groq] OK via {model}")
                return result
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read().decode()[:200]}"
                print(f"[groq] {model} attempt {attempt}: {last}")
                if e.code in (400, 401, 404):
                    break
                if e.code == 403:
                    blocked = True
                    break
                time.sleep(4)
            except Exception as e:
                last = str(e)
                print(f"[groq] {model} attempt {attempt}: {last}")
                time.sleep(4)
        if blocked:
            try:
                print(f"[groq] firewall blocked Python — trying curl for {model}")
                result = _groq_call_curl(model, prompt, key)
                print(f"[groq] OK via curl ({model})")
                return result
            except Exception as e:
                last = str(e)
                print(f"[groq] curl {model}: {last}")
    raise RuntimeError(f"Groq failed on all models. Last error: {last}")

def gemini_json(prompt):
    key = os.environ["GEMINI_API_KEY"]
    base = "https://generativelanguage.googleapis.com/v1beta"
    req = urllib.request.Request(f"{base}/models?key={key}&pageSize=100")
    listing = json.loads(urllib.request.urlopen(req, timeout=60).read())
    usable = [m["name"].split("/")[-1] for m in listing.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])
              and "flash" in m["name"] and "embedding" not in m["name"]
              and "image" not in m["name"] and "tts" not in m["name"]
              and "live" not in m["name"] and "native-audio" not in m["name"]]
    if not usable:
        raise RuntimeError(f"No usable Gemini models for this key. Got: {str(listing)[:400]}")
    model = "gemini-flash-latest" if "gemini-flash-latest" in usable else usable[0]
    print(f"[gemini] using model: {model}")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}}
    last = None
    for attempt in range(1, 5):
        try:
            print(f"[gemini] attempt {attempt}/4")
            req = urllib.request.Request(f"{base}/models/{model}:generateContent?key={key}",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            out = json.loads(urllib.request.urlopen(req, timeout=120).read())
            return _extract_json(out["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode()[:300]}"
            print(f"[gemini] attempt {attempt}: {last}")
            if e.code not in (429, 500, 502, 503, 504):
                raise
            if attempt < 4:
                time.sleep(25)
        except Exception as e:
            last = str(e)
            print(f"[gemini] attempt {attempt}: {last}")
            if attempt < 4:
                time.sleep(25)
    raise RuntimeError(f"Gemini failed after 4 attempts. Last: {last}")

def llm_json(prompt):
    try:
        return groq_json(prompt)
    except Exception as e:
        print(f"[llm] Groq failed: {e} — trying Gemini backup")
    return gemini_json(prompt)

# ---------------------------------------------------------------- images
def gen_image(prompt, out):
    acct = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    key = os.environ["CLOUDFLARE_API_TOKEN"]
    print(f"[img] using account id: {acct[:4]}... ({len(acct)} chars)")
    models = [
        ("flux", "@cf/black-forest-labs/flux-1-schnell",
         {"prompt": prompt, "steps": 4}),
        ("sdxl", "@cf/stabilityai/stable-diffusion-xl-base-1.0",
         {"prompt": prompt, "width": 576, "height": 1024}),
    ]
    last = None
    limit_hits = 0
    for name, model, body in models:
        for attempt in range(1, 4):
            try:
                print(f"[img] {name} attempt {attempt}/3")
                req = urllib.request.Request(
                    f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"})
                resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
                if not resp.get("success"):
                    raise ValueError(f"API error: {str(resp)[:150]}")
                img_b64 = resp.get("result", {}).get("image")
                if not img_b64:
                    raise ValueError(f"no image in response: {str(resp)[:150]}")
                with open(out, "wb") as f:
                    f.write(base64.b64decode(img_b64))
                if out.stat().st_size > 10000:
                    print(f"[img] OK via {name}")
                    return
                raise ValueError("image file too small")
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read().decode()[:200]}"
                print(f"[img] {name} attempt {attempt}: {last}")
                low = last.lower()
                is_daily = (e.code == 402 or
                            (e.code == 429 and any(k in low for k in ("daily", "limit", "quota", "neuron"))))
                if is_daily:
                    limit_hits += 1
                    if limit_hits >= 2:
                        raise DailyLimitError(f"Cloudflare free daily limit reached. Last: {last[:150]}")
                elif e.code in (401, 403, 404):
                    break
                if attempt < 3:
                    time.sleep(5)
            except Exception as e:
                last = str(e)
                print(f"[img] {name} attempt {attempt}: {last}")
                if attempt < 3:
                    time.sleep(5)
            finally:
                if out.exists() and out.stat().st_size <= 10000:
                    out.unlink()
    raise RuntimeError(f"image generation failed for {out.name}. Last: {last}")

# ---------------------------------------------------------------- memory
def load_state():
    f = ROOT / "memory" / "series_state.json"
    try:
        return json.loads(f.read_text())
    except FileNotFoundError:
        return None
    except Exception:
        bak = f.with_suffix(".json.bak")
        print("[memory] memory file unreadable — archiving it and starting a fresh series")
        try:
            f.rename(bak)
        except Exception:
            pass
        return None

# ---------------------------------------------------------------- write
def cmd_write(ep_id, topic):
    ep = EPS / ep_id
    ep.mkdir(parents=True, exist_ok=True)
    out = ep / "script.json"
    if out.exists():
        print(f"[write] {out} already exists — skipping (idempotent)")
        return
    st = load_state()
    limit = series_len()
    if st and hub.trigger_flag("new_series"):
        print("[write] OpsHub asked for a new series - archiving the current arc")
        try:
            (ROOT / "memory" / "series_state.json").replace(
                ROOT / "memory" / "series_state.prev.json")
        except OSError:
            pass
        st = None
    fresh = not (st and not st.get("finished") and st.get("episode_number", 0) < limit)
    era = ""
    conflict = ""
    if not fresh:
        n = st["episode_number"] + 1
        memory_text = (
            f"'{st['series_title']}' DİZİSİNİ SÜRDÜR — bu bölüm {n}/{limit}.\n"
            f"{st['episode_number']}. bölümün özeti: {st.get('last_recap', '')}\n"
            f"O bölüm şu cliffhanger ile bitti: {st.get('ending_cliffhanger', '')}\n"
            "AYNI hikâyeyi AYNI karakterlerle sürdür. Baştan başlama.\n"
            f"Dizinin karakteri (DEĞİŞMESİN): {st.get('character', '')}\n"
            f"Dizinin dönemi/mekânı (DEĞİŞMESİN): {st.get('seed', '')}"
        )
        if st.get("last_hook"):
            memory_text += (f"\nÖnceki bölümün bitiş kalıbı: {st['last_hook']}. "
                            "Bu bölümde DİĞER iki kalıptan birini kullan.")
    else:
        n = 1
        seed_idx, era, conflict = _next_seed()
        print(f"[write] new series seed #{seed_idx}: {era}")
        memory_text = (
            "YEPYENİ BİR DİZİ BAŞLAT (henüz hiçbir şey yok).\n"
            f"TOHUM — dönem ve mekân: {era}\n"
            f"ÇEKİRDEK ÇATIŞMA: {conflict}\n"
            "Özgün bir öncül, özgün karakterler ve özgün bir dizi adı uydur; bu tohuma bağlı kal.\n"
            f"Dizi {limit} bölüm sürecek, bu yüzden ilerideki bölümler için tohum ek. Türkçe bir isim seç.\n"
            "Karakter her bölümde AYNI kişi kalacak; memory nesnesine şu iki alanı da ekle ve\n"
            "bundan sonraki bölümlerde birebir kullanılacak şekilde yaz:\n"
            "  \"character\": \"<İNGİLİZCE tek satır: yüz, yaş, saç, kıyafet, ayırt edici iz>\"\n"
            "  \"character_pov\": \"<İNGİLİZCE tek satır: birinci şahıs POV, yüz görünmez>\""
        )
    if topic:
        memory_text += f"\nYapımcının notu (buna uy): {topic}"

    bible = dict(DEFAULT_BIBLE)
    if not fresh and st:
        bible["character"] = st.get("character") or bible["character"]
        bible["character_pov"] = st.get("character_pov") or bible["character_pov"]

    last_flavor = (st or {}).get("last_flavor", "")
    options = [f for f in FLAVORS if f != last_flavor] or list(FLAVORS)
    flavor = options[(n - 1) % len(options)]

    print(f"[write] series episode #{n} of {limit} — tat: {flavor}")
    prompt = build_prompt(memory_text, bible, flavor, last_flavor, fresh)
    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            probe = prompt
            if last_err:
                probe += (f"\n\nÖNEMLİ: önceki cevabın reddedildi ({last_err}). "
                          f"TAM {TOTAL_SHOTS} çekim ve eksiksiz memory nesnesi döndür.")
            data = llm_json(probe)
            if isinstance(data.get("shots"), list) and 18 <= len(data["shots"]) <= 24:
                break
            last_err = f"only {len(data.get('shots', []))} shots"
            data = None
        except Exception as e:
            last_err = str(e)[:150]
            data = None
    if data is None:
        raise RuntimeError(f"script writer failed after 3 attempts. Last problem: {last_err}")

    for k in ("title", "hook", "caption"):
        data.setdefault(k, "isimsiz")
    data.setdefault("hashtags", ["#hikaye", "#kesfet", "#fyp"])
    data.setdefault("flavor", flavor)
    for i, s in enumerate(data["shots"], 1):
        s.setdefault("level", (i - 1) // SHOTS_PER_LEVEL + 1)
        s.setdefault("beat", "")
        s.setdefault("time", "")
        s.setdefault("mode", ["wide", "pov", "closeup"][(i - 1) % 3])
        if s.get("mode") not in ("wide", "pov", "closeup"):
            s["mode"] = ["wide", "pov", "closeup"][(i - 1) % 3]
        s.setdefault("narration", "")
        s.setdefault("visual", DEFAULT_BIBLE["character"])
    data["shots"] = [s for s in data["shots"] if str(s.get("narration", "")).strip()]
    if not data["shots"]:
        raise RuntimeError("script has no usable shots (all narrations empty)")

    mem = data.get("memory", {}) if isinstance(data.get("memory"), dict) else {}
    if not mem:
        print("[memory] model omitted the memory object — rebuilding it from the script")
        all_narr = " ".join(s["narration"] for s in data["shots"])
        mem = {"series_title": str(data.get("title") or "İsimsiz Dizi")[:120],
               "this_episode_recap": all_narr[:600],
               "ending_cliffhanger": data["shots"][-1]["narration"][:400],
               "ending_pattern_used": "",
               "flavor": data.get("flavor", flavor)}
    series_title = str(mem.get("series_title") or data.get("title") or "İsimsiz Dizi")[:120]
    st2 = {"title": series_title,          # sync-state.yml reads this field
           "series_title": series_title,
           "episode_number": n,
           "last_recap": str(mem.get("this_episode_recap", ""))[:600],
           "ending_cliffhanger": str(mem.get("ending_cliffhanger", ""))[:400],
           "last_hook": str(mem.get("ending_pattern_used", ""))[:60],
           "last_flavor": flavor,   # the flavor WE picked, so rotation can't drift if the model writes something else
           "character": str(mem.get("character") or bible["character"])[:700],
           "character_pov": str(mem.get("character_pov") or bible["character_pov"])[:700],
           "seed": era if fresh else str((st or {}).get("seed", ""))[:200],
           "finished": n >= limit}
    (ROOT / "memory").mkdir(exist_ok=True)
    (ROOT / "memory" / "series_state.json").write_text(json.dumps(st2, indent=2, ensure_ascii=False))
    print(f"[memory] episode {n}/{limit} recorded"
          + (" — SERIES COMPLETE! the next /start begins a brand-new series" if st2["finished"] else ""))
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[write] OK -> {out} ({len(data['shots'])} shots)")

# ---------------------------------------------------------------- produce
def cmd_produce(ep_id):
    ep = EPS / ep_id
    sc = json.loads((ep / "script.json").read_text())
    shots = sc["shots"]
    imgs, auds, segs = ep / "images", ep / "audio", ep / "segs"
    for d in (imgs, auds, segs):
        d.mkdir(parents=True, exist_ok=True)
    bible = dict(DEFAULT_BIBLE)
    _state = load_state() or {}
    if _state.get("character"):
        bible["character"] = _state["character"]
    if _state.get("character_pov"):
        bible["character_pov"] = _state["character_pov"]
    for i, s in enumerate(shots, 1):
        out = imgs / f"L{i}.png"
        if out.exists() and out.stat().st_size > 10000:
            print(f"[img] L{i} cached"); continue
        prompt = f"{s['visual']}, {bible['style']}"
        gen_image(prompt, out)
        print(f"[img] L{i} ok ({s.get('mode', 'wide')}/{s.get('beat', '')})")
    for i, s in enumerate(shots, 1):
        out = auds / f"L{i}.mp3"
        if out.exists() and out.stat().st_size > 1000:
            print(f"[tts] L{i} cached"); continue
        txt = s["narration"]
        for attempt in range(1, 4):
            try:
                sh(["edge-tts", "--voice", VOICE, "--text", txt, "--write-media", out])
                break
            except Exception as e:
                print(f"[tts] L{i} attempt {attempt}: {e}")
                time.sleep(3)
        else:
            raise RuntimeError(f"narration L{i} failed after 3 attempts")
        print(f"[tts] L{i} ok")
    seg_files = []
    for i, s in enumerate(shots, 1):
        sf = segs / f"seg{i}.mp4"
        seg_files.append(sf)
        if sf.exists():
            print(f"[seg] {i} cached"); continue
        a = dur(auds / f"L{i}.mp3")
        frames = int(a * FPS) + FPS
        mode = s.get("mode", "wide")
        # per-mode camera move: arrival pushes in, POV drifts in slightly, outcome pulls back
        if mode == "closeup":
            zexpr = "if(lte(zoom,1.0),1.25,max(1.001,zoom-0.0015))"
        elif mode == "pov":
            zexpr = "min(zoom+0.0010,1.12)"
        else:
            zexpr = "min(zoom+0.0015,1.25)"
        # NO TEXT ON SCREEN — v7's drawtext caption/label filters were removed on purpose.
        vf = ("scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
              f"zoompan=z='{zexpr}':d={frames}:s=720x1280:fps={FPS},format=yuv420p")
        try:
            sh(["ffmpeg", "-y", "-loop", "1", "-i", imgs / f"L{i}.png", "-i", auds / f"L{i}.mp3",
                "-vf", vf, "-t", f"{a:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", sf])
        except Exception:
            if sf.exists():
                sf.unlink()
            raise
        print(f"[seg] {i} ok ({a:.1f}s, {mode})")
    lst = ep / "list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in seg_files))
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
        "-c", "copy", ep / "video.mp4"])
    meta = {"episode": ep_id, "language": "tr", "voice": VOICE,
            "title": sc["title"], "caption": sc["caption"], "hashtags": sc["hashtags"],
            "shots": len(shots), "flavor": sc.get("flavor", ""),
            "platforms": {"youtube": "private", "tiktok": "draft"},
            "captions_burned_in": False,
            "note": "No text on screen; YouTube/TikTok generate the subtitles at upload.",
            "ai_disclosure": "Created with AI tools"}
    (ep / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    size = (ep / "video.mp4").stat().st_size / 1e6
    print(f"[produce] OK -> {ep / 'video.mp4'} ({size:.1f} MB, {len(shots)} shots)")

# ---------------------------------------------------------------- notify
def _emit_ready(sc, ep_id):
    """Feed OpsHub: one episode_ready event + the refreshed series state."""
    st = load_state() or {}
    series = st.get("series_title") or sc.get("title", "series")
    ep_no = st.get("episode_number") or 0
    hub.emit_episode_ready(
        series_id=series, episode_number=ep_no,
        title=sc.get("title", ""), hook_type=st.get("last_hook", ""),
        engine_version="v8", episode=ep_id, flavor=sc.get("flavor", ""),
        shots=len(sc.get("shots", [])), language="tr", seed=st.get("seed", ""))
    hub.emit_series_state(
        series_id=series, episode_number=ep_no,
        finished=bool(st.get("finished")), max_episodes=series_len(),
        title=sc.get("title", ""), last_hook=st.get("last_hook", ""),
        engine_version="v8", language="tr")


def cmd_notify(ep_id):
    ep = EPS / ep_id
    sc = json.loads((ep / "script.json").read_text())
    text = (f"🎬 {sc['title']}\n\n{sc.get('hook', '')}\n\n{sc['caption']}\n\n"
            f"{' '.join(sc['hashtags'])}\n\nbölüm: {ep_id} · {len(sc.get('shots', []))} çekim")
    vid = ep / "video.mp4"
    size_mb = vid.stat().st_size / 1e6
    api = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
    if size_mb >= 49:
        r = requests.post(f"{api}/sendMessage",
                          data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text})
        print(f"[notify] {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        print(f"[notify] video is {size_mb:.0f} MB — download it from the run's Artifacts")
        _emit_ready(sc, ep_id)
        return
    with open(vid, "rb") as f:
        r = requests.post(f"{api}/sendVideo",
                          data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": text[:1024]},
                          files={"video": f}, timeout=600)
    print(f"[notify] {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    _emit_ready(sc, ep_id)

def _tell(text):
    try:
        requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                      data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:4000]}, timeout=60)
    except Exception:
        pass

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["write", "produce", "notify"])
    p.add_argument("--episode", required=True)
    p.add_argument("--topic")
    a = p.parse_args()
    if not a.topic:
        a.topic = hub.trigger_topic()
    if a.topic:
        print(f"[pipeline] brief from {hub.why()}: {a.topic}")
    try:
        {"write": lambda: cmd_write(a.episode, a.topic),
         "produce": lambda: cmd_produce(a.episode),
         "notify": lambda: cmd_notify(a.episode)}[a.cmd]()
    except DailyLimitError:
        now = datetime.datetime.now(datetime.timezone.utc)
        reset = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        hrs = (reset - now).total_seconds() / 3600
        reset_s = reset.strftime("%Y-%m-%d %H:%M UTC")
        open("/tmp/limit_flag.txt", "w").write(reset.strftime("%Y-%m-%dT%H:%M:%SZ"))
        print(f"[limit] daily free limit hit — resets {reset_s} (~{hrs:.0f}h)")
        _tell(f"🛑 Daily free quota is used up.\n\nResets at {reset_s} (~{hrs:.0f}h).\n\n"
              "The next episode will retry AUTOMATICALLY about an hour after the reset — "
              "nothing was lost. You can also start it by hand with /start.")
        sys.exit(1)
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        _tell(f"❌ run failed ({a.cmd})\n\n{tb[-2500:]}")
        sys.exit(1)
