# catia_diff — 2B teknik resim denetim ajanları

2B teknik resimlerdeki **eksik, hatalı veya tutarsız** unsurları bulan çok ajanlı
bir denetim aracı. DXF ve PDF girdilerini vektörel olarak ayrıştırır, taranmış
sayfalar için Claude Vision'a düşer, ISO / ASME Y14.5 kurallarını uygular ve
önceliklendirilmiş, işaretlenmiş bir rapor üretir.

*A multi-agent auditor for 2D engineering drawings: vector-first parsing (DXF /
PDF), Claude Vision fallback for scans, ISO / ASME rule checks, and a
prioritised, marked-up report.*

---

## Neler bulur? / What it finds

| Aile | Örnek bulgular |
| --- | --- |
| Ölçülendirme (`DIM001–DIM010`) | ölçülendirilmemiş delik, kapalı ölçü zinciri (aşırı ölçülendirme), **ölçü metni geometriyle uyuşmuyor**, eksik ⌀ sembolü, çerçeve dışı gösterim, üst üste binen ölçüler |
| Tolerans (`TOL001–TOL010`) | toleranssız ölçü (genel tolerans notu yoksa majör), ters/sıfır tolerans aralığı, ondalık hane uyumsuzluğu, gerçekçi olmayan dar tolerans, karışık gösterim, **ISO 2768 sayısal denetimleri** (aşağıya bakın) |
| Geometrik tolerans (`GDT001–GDT011`) | tanımsız datum referansı, datumsuz diklik/konum toleransı, datumlu biçim toleransı, tekrarlanan datum, teorik ölçüsü olmayan konum toleransı, genel geometrik toleranstan geniş çerçeve |
| Semboller (`SYM001–SYM005`) | değersiz yüzey sembolü, ölçüsüz kaynak sembolü, adımsız aralıklı kaynak, gerçekçi olmayan Ra |
| Antet (`TB001–TB011`) | antet yok, zorunlu alan boş, "TBD" yer tutucusu, standart dışı ölçek, tarihsiz revizyon, çizen = onaylayan, izdüşüm yöntemi yok, birim yok, sayfa numarası tutarsız |
| Tutarlılık (`CON001–CON006`) | karışık birimler, ölçek–geometri uyuşmazlığı, sayfalar arası resim no çakışması, görsel çıkarım yapılmadan okunamayan sayfa |

Tam liste: `catia-diff rules --lang tr` (53 kural)

### ISO 2768 sayısal olarak

`ISO 2768-mK` gibi bir genel tolerans notu, tablolarıyla birlikte
`catia_diff.standards.iso2768` içinde veridir; not okunduğu anda her ölçü için
izin verilen sapma sayıya dönüşür:

| Kural | Ne denetler | Örnek bulgu |
| --- | --- | --- |
| `TOL006` | Notun sınıf harfi var mı, geçerli mi | "ISO 2768" — sınıf belirtilmemiş, sayı türetilemiyor |
| `TOL007` | Nominal ölçü tablonun kapsamında mı | 0,3 mm ölçü: tablo 0,5 mm'den başlıyor → sapma ölçünün üzerinde yazılmalı |
| `TOL008` | Yazılı tolerans genel toleranstan geniş mi | 40 mm'de ±1,5; `ISO 2768-m` ±0,3 veriyor |
| `TOL009` | Yazılı tolerans genel toleransın aynısı mı | 56 mm'de ±0,3 — gereksiz tekrar |
| `TOL010` | Kapalı zincirde tolerans birikimi toplam ölçüye sığıyor mu | 12+56+12 zinciri ±0,7 biriktiriyor, toplam ölçü ±0,3 veriyor |
| `GDT011` | Çerçeve, ISO 2768-2 genel geometrik toleransından geniş mi | Düzlemsellik 0,8; sınıf K 80 mm için 0,2 veriyor |

Tablolar ISO 2768-1 (boyut/açı, sınıf f·m·c·v) ve ISO 2768-2 (düzlemsellik–
doğrusallık, diklik, simetri, dairesel salgı; sınıf H·K·L) ile türetilmiş
kuralları (yuvarlaklık ≤ salgı, paralellik = maks(boyut toleransı, düzlemsellik))
içerir. Standardın tanımlamadığı yerler (silindiriklik, konum, açısallık, profil)
`None` döner ve kural sessiz kalır — tahmin üretilmez. `in` biriminde çizilmiş
resimlerde değerler mm'ye çevrilip geri dönüştürülür.

Tabloların tamamı, kural eşlemesi, varsayımlar ve Python API'si:
[`docs/ISO2768.md`](docs/ISO2768.md).

## Kurulum / Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"       # veya: pip install -e ".[dxf,pdf,raster,report]"
```

Çekirdek yalnızca `pydantic` ister. İsteğe bağlı ekler:
`dxf` (ezdxf) · `pdf` (PyMuPDF) · `raster` (Pillow, NumPy) · `cv` (OpenCV) ·
`llm` (anthropic) · `report` (Jinja2). Eksik olan bir ek yalnızca ilgili yolu
kapatır, programı durdurmaz.

Claude Vision için kimlik: `export ANTHROPIC_API_KEY=...` (veya `ant auth login`).

## Kullanım / Usage

```bash
# Örnek resmi üret (kasıtlı hatalarla) ve denetle
python examples/generate_sample_drawing.py examples/sample_plate.dxf
catia-diff audit examples/sample_plate.dxf --lang tr --out reports

# ISO 2768-mK notlu varyant: sayısal genel tolerans denetimlerini tetikler
python examples/generate_sample_drawing.py examples/sample_2768.dxf --iso2768
catia-diff audit examples/sample_2768.dxf --lang tr --out reports

# Taranmış PDF: metin katmanı yoksa otomatik olarak Claude Vision devreye girer
catia-diff audit tarama.pdf --vision auto --dpi 300

# ASME profili, yalnızca kritik bulgular, CI için sert çıkış kodu
catia-diff audit part.dxf --profile ASME --min-severity critical --fail-on critical

# Kural seçimi
catia-diff audit part.dxf --only DIM001,DIM003
catia-diff audit part.dxf --disable TOL001 --category gdt,title_block
```

Çıkış kodları: `0` temiz · `1` `--fail-on` eşiğinde bulgu var · `2` dosya
okunamadı.

Üretilen dosyalar: `<ad>_audit.json`, `<ad>_audit.md`, `<ad>_audit.html`
(filtrelenebilir kartlar, açık/koyu tema) ve sayfa başına
`<ad>_sheetN_overlay.png` (bulgular numaralandırılmış kutularla işaretli).

### Python API

```python
from catia_diff import AuditConfig, Profile, Severity, audit_file

report = audit_file("part.dxf", AuditConfig(profile=Profile.ISO, language="tr"))
print(report.summary_line("tr"))                    # Kritik: 3 | Majör: 12 …
for finding in report.by_severity(Severity.CRITICAL):
    print(finding.rule_id, finding.localized_message("tr"), finding.evidence.bbox)
```

## Girdi biçimleri / Input formats

| Biçim | Yol | Ne elde edilir |
| --- | --- | --- |
| **DXF** | `ezdxf` | En yüksek doğruluk: ölçüler gerçek ölçülen değerle birlikte gelir, tolerans üstünü yazma (text override) tespit edilebilir, antet blok öznitelikleriyle okunur |
| **PDF (vektörel)** | `PyMuPDF` | Metin aralıkları + vektör geometrisi; çoğu CAD çıktısı bu gruba girer |
| **PDF (taranmış) / PNG, JPG, TIFF** | raster → Claude Vision | Sayfa temizlenir, döşenir, yapılandırılmış çıktı ile yazıya dökülür |
| **DWG** | — | Kapalı biçim: önce DXF'e çevirin (`ODAFileConverter <in> <out> ACAD2018 DXF 0 1`) |

> **Neden vektör öncelikli?** "Ölçü 25 yazıyor ama geometri 30" gibi en pahalı
> hatalar yalnızca ölçünün gerçek değerinin bilindiği vektörel girdide
> yakalanabilir. Raster yol tam bir yedektir, eşdeğeri değildir — bu yüzden
> görsel çıkarım yapılmayan taranmış sayfa `CON005` ile ayrıca raporlanır.

## Mimari / Architecture

```
Orchestrator
 ├── Extraction Agent      DXF/PDF/raster → DrawingDocument (+ Claude Vision)
 ├── Dimensioning Agent    DIM · TOL · GDT · SYM kuralları   ┐ paralel
 ├── Title Block Agent     TB kuralları                      │ çalışır
 ├── Consistency Agent     CON kuralları                     ┘
 └── Report Agent          tekilleştir → önceliklendir → overlay + JSON/MD/HTML
```

Ayrıntılar, akış diyagramı, mesaj protokolü ve veri modeli:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · ISO 2768 sayısal referansı:
[`docs/ISO2768.md`](docs/ISO2768.md).

## Yeni kural ekleme / Adding a rule

```python
# src/catia_diff/rules/dimensioning.py
@register
class MyRule(Rule):
    meta = RuleMeta(
        id="DIM011",
        title="Chamfer without an angle",
        title_tr="Açısı belirtilmemiş pah",
        severity=Severity.MAJOR,
        category=Category.DIMENSIONING,
        standards=("ISO 129-1 §9",),
    )

    def check(self, target: Sheet, ctx: RuleContext):
        for dim in target.dimensions:
            if dim.kind is DimensionKind.CHAMFER and "°" not in dim.text:
                yield self.finding(
                    message=f"Chamfer {dim.id} has no angle.",
                    message_tr=f"{dim.id} pahında açı belirtilmemiş.",
                    sheet_index=target.index,
                    bbox=dim.bbox,
                    object_ids=[dim.id],
                )
```

Kayıt otomatiktir; CLI, rapor ve testler kuralı hemen görür.

## Geliştirme / Development

```bash
pytest -q                      # 211 test, isteğe bağlı bağımlılık yoksa atlanır
pytest --cov=catia_diff        # ~%89 kapsam
ruff check src tests examples
```

Testler ağ erişimi gerektirmez: Claude çağrıları `MockVisionModel` ve sahte bir
istemci ile doğrulanır.

## Sınırlar / Known limits

* Görünüş (view) ayrıştırma etiket tabanlıdır; gerçek görünüş kümeleme yoktur —
  bu nedenle "aynı unsur iki görünüşte farklı ölçülendirilmiş" denetimi henüz yok.
* Unsur–ölçü eşleştirmesi ve kapalı zincir (raster yolda) sezgiseldir;
  bulgular `confidence < 1.0` ile işaretlenir.
* DWG doğrudan okunmaz.
* ISO 286 geçme sınıfları (H7, g6) sayısal olarak çözülmez; bu toleranslar
  "bilinmiyor" sayılır ve sayısal karşılaştırmalara girmez.
* ISO 2768-1 açı tablosu açının kısa kenarına göre indekslidir; 2B gösterim bunu
  vermediği için en geniş satır (en güvenli varsayım) kullanılır. Aynı şekilde
  ISO 2768-2 denetimi, çerçevenin ait olduğu unsurun boyu yerine sayfadaki en
  büyük ölçüyü alır — yanlış pozitif yerine eksik rapor tarafında kalır.
