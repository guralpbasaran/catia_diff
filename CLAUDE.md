# CLAUDE.md

Bu dosya, depoda çalışan Claude Code oturumları için proje hafızasıdır.
Amaç: kod tabanının nasıl kurulduğunu, bir denetim kuralının nasıl eklendiğini ve
hangi doğruluk disiplinlerinin pazarlık dışı olduğunu tek yerde tutmak.

*Project memory for Claude Code sessions: layout, conventions, and the accuracy
rules that must not be relaxed.*

---

## Proje ne yapar

`catia_diff`, 2B teknik resimlerdeki eksik / hatalı / tutarsız unsurları bulan çok
ajanlı bir denetim aracıdır. Girdi **vektör-öncelikli** işlenir (DXF → `ezdxf`,
vektör PDF → PyMuPDF); yalnızca taranmış sayfalarda Claude Vision'a düşülür.
Çıktı: önceliklendirilmiş bulgular + JSON / Markdown / HTML rapor + işaretli PNG.

Kullanıcıya dönük metinler **çift dillidir (TR/EN)**; önem dereceleri
Kritik / Majör / Minör / Bilgi.

## Kurulum ve komutlar

```bash
python -m venv .venv && .venv/bin/pip install -e ".[all,dev]"

.venv/bin/python -m pytest            # 446 test
.venv/bin/python -m pytest --cov=src/catia_diff --cov-report=term-missing
.venv/bin/ruff check src tests examples run_ui.py
.venv/bin/mypy src

catia-diff audit examples/sample_plate.dxf --lang tr --out reports
catia-diff ui --port 8050 --lang tr   # tarayıcı panosu (Dash)
python run_ui.py                      # aynı pano, IDE'den tek tık (parametresiz)
catia-diff rules --lang tr            # 73 kural
catia-diff formats
python examples/generate_sample_drawing.py /tmp/tam.dxf --complete --fits --iso2768
```

`.venv/bin/python` kullanın: sistem Python'ında paket kurulu değildir.
Testler `conftest.py` üzerinden `src`'i yola ekler; ad hoc betiklerde
`PYTHONPATH=src` gerekir.

## Dizin haritası

| Yol | Sorumluluk |
| --- | --- |
| `agents/` | Orkestratör + ajanlar (`extraction`, `dimensioning`, `title_block`, `consistency`, `report`). Ajanlar arası protokol `models/messages.py`'deki `AgentTask` / `AgentResult` zarflarıdır; hata her ajanda yalıtılır, biri düşerse denetim sürer. Denetleyici ajanlar `ThreadPoolExecutor` ile paralel koşar (`--sequential` kapatır). |
| `extract/` | Format başına çıkarıcı + `registry`. Ortak metin grameri `text_parsing.py`'dedir — **her kaynak (DXF/PDF/Vision) aynı gramerden geçer.** |
| `llm/` | Claude soyutlaması: `base.VisionClient` arayüzü, `anthropic_client`, testler için `mock`. Kural kodu Anthropic SDK'sını doğrudan görmez. |
| `models/` | Pydantic v2 alan modeli. Çıkarım ile denetim arasındaki **tek sözleşme** burasıdır. |
| `rules/` | Kural motoru (`base.py`) + aile başına bir modül. `analysis.py`, `constraints.py`, `projection.py`, `markers.py` ve `patterns.py` kural içermez, saf yardımcıdır. |
| `standards/` | Makine-okunur standart verisi: `iso2768.py` (genel toleranslar), `iso286.py` (limitler ve geçmeler). |
| `reporting/` | `normalize` (sıralama/tekilleştirme), `render` (JSON/MD/HTML), `overlay` (numaralı kutular). Kutu numaraları `normalize.overlay_numbers`'dan gelir; panodaki `No` sütunu da aynı kaynağı okur. |
| `ui/` | Dash panosu. `service`/`presenters`/`charts`/`theme` Dash'e bağımlı **değildir**; `app.py` yalnızca yerleşim ve bağlantıdır. Geri çağırmalar ince kalır: her biri bir `*_view` fonksiyonuna devreder, test o fonksiyonu çağırır (tarayıcı gerekmez). |

## Kural eklemek

Bir denetim eklemek = **bir sınıf eklemek**; başka dosya değişmez.

```python
@register
class MyRule(Rule):
    meta = RuleMeta(
        id="DIM019",            # aile içinde sıradaki boş numara
        title="…",              # EN
        title_tr="…",           # TR
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §4.1",),
        description="…",
    )

    def check(self, target: Sheet, ctx: RuleContext) -> Iterable[Finding]:
        ...
```

- Kural id'leri aile başına sıralıdır (`DIM`, `CRV`, `REF`, `TOL`, `GDT`, `SYM`, `TB`, `CON`).
- Pahalı türetmeler `RuleContext` üzerinde önbelleklenir (genel tolerans, kapsam
  analizi, blanket notlar) — kuralın içinde yeniden hesaplamayın.
- `scope = "document"` sayfa yerine tüm belgeyi alır.
- Kayıt yükleme kilitlidir (`threading.Lock`): paralel ajanlar yarı dolu bir
  kayıt görmesin diye `_LOADED` bayrağı **import'lardan sonra** set edilir.
  Bu sırayı bozmayın; geçmişte kuralların sessizce kaybolmasına yol açtı.
- Her yeni kural, hem tetiklendiği hem de **sessiz kalması gerektiği** durum için
  test ister.

## Kısıt grafiği (eksik ölçülendirme)

`rules/constraints.py` çekirdek fikirdir: her görünüşte, her eksende geometrinin
referans koordinatları düğüm, ölçüler kenardır.

- kapsayan ağaç → tam ölçülendirilmiş
- `bileşen − 1` → o kadar **eksik** ölçü (`DIM011`, `DIM012`)
- çevrim sayısı → o kadar **fazla** ölçü (`DIM003`, `TOL010`)

Grafiğin göremediği üç eksik ayrı kurallardadır: `DIM016` kalınlık (tek görünüş,
üçüncü boyut hiçbir yerde), `DIM017` delik dairesi çapı, `DIM018` pah ölçüsü.
Delik dairesi tanımı dardır — eş yarıçaplı, çembersel **ve eşit açısal
bölüntülü** ≥3 delik; dikdörtgen köşeleri bu tanıma girmez. `DIM011` delik
dairesindeki deliklerde susar (`rules/patterns.py`).

`rules/projection.py` bunu görünüşler arasına taşır: ortografik olarak hizalı iki
görünüş bir ekseni paylaşır. O eksen komşu görünüşte ölçülendirilmişse burada
**eksik sayılmaz** (miras — bu olmadan her doğru çok görünüşlü resim yanlış
pozitif üretir), paylaşılan uzunluk için iki görünüş farklı şey söylüyorsa
`CRV001`/`CRV002`/`CRV003` devreye girer. `%10`'dan fazla ayrışan çiftler
karşılaştırılmaz: aynı şeyi gösterdikleri kanıtlanamaz.

Görünüş ayrıştırma (`rules/views.py`) **çıkarım sırasında bir kez** koşar; paralel
denetleyicilerle yarış olmaması için bunu kural içine taşımayın.
Bu analiz aralık verisi ister, dolayısıyla **DXF-öncedir**; raster yolda
çalıştırılmaz ve raporda nedeni yazılır (`CON005`).

## Referans bütünlüğü (sembolik referanslar)

`rules/references.py` + `rules/markers.py`: kesit işareti → görünüş, not
numarası → not, sayfa numarası → sayfa. Çözümleme **belge genelindedir**.

- `parse_view_caption` başa demirlidir (başlık), `view_references` metinde arar
  (gönderme) — çıkarıcı "DETAY" içeren her metni görünüş dosyaladığı için bu
  ayrım kuralın tamamını belirler.
- Tek harfli işaret **yalnızca balon geometrisiyle** sayılır: tek harf, datum
  sembolünün de yazılışıdır.
- `REF002` kapılıdır: belgede hiç işaret tanınamadıysa çalışmaz.
- "Hayali" geometri (kesit düzlemi, phantom) ne kapsam düğümü üretir ne de
  görünüş sınırını genişletir (`analysis.is_imaginary`).

## Doğruluk disiplinleri (pazarlık dışı)

1. **Tahmin yok.** Kapsam dışı girdi `None` döner ve bulgu olarak raporlanır
   (`CON005`, `TOL007`, `TOL011`). Uydurulmuş bir tolerans, sessiz bir hatadır.
2. **Tablolar formüle karşı doğrulanır.** `standards/` içindeki her hücre,
   standardın kendi üretici formülüyle testte karşılaştırılır
   (`max(1 µm, %8–12)` bandı) — bu bir transkripsiyon-hatası dedektörüdür.
   Bant dışı bir hücre "düzeltilmez", **araştırılır**: ISO 286'nın 0–3 mm aralığı
   standartça sabittir, bu yüzden hariç tutulup belgelenmiştir.
3. **Türetilebileni türet.** Delik sapmaları tablodan değil `EI = −es` ve Δ
   kuralından hesaplanır; ikinci bir tablo ikinci bir hata kaynağıdır.
4. **Yanlış pozitif = güven kaybı.** `--complete` örnek varyantı kapsam
   kurallarından **hiç** bulgu üretmemelidir; regresyon testi budur.
5. **Dokümandaki her tablo ve her örnek koddan üretilir**, elle yazılmaz;
   yayımlanmadan önce çalıştırılır.
6. Negatif sıfır normalize edilir; rapor asla "−0" yazmaz.
7. **Renk tek yerde, ölçülerek seçilir.** `SEVERITY_COLORS` overlay, HTML rapor
   ve panoda ortaktır; komşu önem renkleri algısal ayrım eşiğinin (normal ve
   renk körü görme) üstünde tutulur ve önem her yüzeyde metinle de yazılır —
   renk tek başına taşıyıcı değildir.
8. Türkçe metinde CSS `text-transform: uppercase` kullanılmaz (İ/I sorunu);
   etiketler okunacakları biçimde yazılır.

## Doküman haritası

| Dosya | İçerik |
| --- | --- |
| `README.md` | Ne bulur, kurulum, hızlı başlangıç |
| `docs/ARCHITECTURE.md` | Ajan protokolü, veri akışı, genişletme noktaları |
| `docs/DIMENSION_COVERAGE.md` | Kısıt grafiği modeli, örnek üzerinde adım adım çözüm |
| `docs/ISO2768.md` | Genel tolerans tabloları ve sayısal denetim kapsamı |
| `docs/ISO286.md` | IT dereceleri, temel sapmalar, geçme karakteri, kapsam sınırları |
| `docs/REFERENCES.md` | Bildirim/kullanım modeli, işaret tespiti, yedi kural, kapı kuralı |

## Git

- Geliştirme dalı: `claude/great-darwin-tlo4yt`; varsayılan dal `main`.
- Commit mesajları ve depo içeriğinde model adı geçmez.
- PR yalnızca açıkça istendiğinde açılır.
