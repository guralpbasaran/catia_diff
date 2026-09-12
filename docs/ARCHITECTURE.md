# Mimari / Architecture

> Bu belge Türkçe ve İngilizce paralel ilerler. / This document runs in Turkish and English.

## 1. Genel akış / Pipeline

```mermaid
flowchart TD
    A[DXF / PDF / PNG] --> O{{Orchestrator}}
    O -->|TaskKind.EXTRACT| E[Extraction Agent]

    subgraph EX[Extraction Agent]
        E1[DxfExtractor<br/>ezdxf] --- E2[PdfExtractor<br/>PyMuPDF] --- E3[ImageExtractor]
        E4{needs_vision?}
        E5[raster: render → preprocess → tile]
        E6[Claude Vision<br/>structured output]
        E7[merge_extraction]
        E4 -->|yes| E5 --> E6 --> E7
    end

    E --> D[(DrawingDocument<br/>pydantic)]
    D --> C1[Dimensioning Agent<br/>DIM / TOL / GDT / SYM]
    D --> C2[Title Block Agent<br/>TB]
    D --> C3[Consistency Agent<br/>CON]
    C1 & C2 & C3 -->|Finding listesi| R[Report Agent]
    R --> R1[normalize: dedupe → cap → sort]
    R --> R2[overlay PNG]
    R --> R3[JSON / Markdown / HTML]
    R --> RPT[(AuditReport)]
```

Aşamalar / Stages:

1. **Extraction** — dosya biçimine göre bir `SourceExtractor` seçilir. Vektörel
   girdide (DXF, metin katmanlı PDF) her şey ayrıştırılır; taranmış sayfalarda
   `needs_vision` işaretlenir ve çok modlu geçiş devreye girer.
2. **Checkers** — üç ajan aynı `DrawingDocument` üzerinde paralel çalışır
   (yalnızca okuma yaparlar, bu yüzden kilit gerekmez).
3. **Reporting** — bulgular tekilleştirilir, önceliklendirilir, sayfa görselleri
   üretilir ve üç formatta rapor yazılır.

## 2. Ajanlar arası protokol / Inter-agent protocol

Ajanlar birbirini doğrudan çağırmaz. Orchestrator her ajana bir `AgentTask`
verir, karşılığında bir `AgentResult` alır:

```python
AgentTask(kind=TaskKind.CHECK_DIMENSIONS, task_id="9f2c…", payload={...})
        ↓
AgentResult(task_id="9f2c…", agent="dimensioning", status=AgentStatus.OK,
            findings=[Finding, …], artifacts={"rules": 30}, warnings=[], duration_ms=12.4)
```

| Alan | Anlamı |
| --- | --- |
| `status` | `ok` / `partial` (örn. vision yok) / `failed` / `skipped` |
| `findings` | Kurallardan çıkan bulgular |
| `artifacts` | Sonraki aşamaya geçen nesneler (`document`, `report`, dosya yolları) |
| `warnings` | Rapor başlığına taşınan uyarılar |
| `duration_ms` | `Agent.execute` tarafından ölçülür |

`Agent.execute()` şablon metottur: süre ölçümü, günlükleme ve **hata
kapsaması** oradadır. Bir ajan çökerse `status=failed` döner, diğer ajanlar
çalışmaya devam eder ve hata raporun uyarılarına düşer — sessizce temiz bir
rapor asla üretilmez.

`AuditContext` ajanların ortak karatahtasıdır: `config`, `source_path`,
`workdir`, çıkarılan `document` ve `vision_model`.

## 3. Veri modeli / Data model

`catia_diff.models` (pydantic v2) çıkarım ile denetim arasındaki sözleşmedir:

```
DrawingDocument
├── source_path, source_format (dxf | pdf_vector | pdf_raster | image)
├── units, metadata, warnings, vision_used
└── sheets: list[Sheet]
    ├── width/height/origin, coordinate_space (y_up | y_down), scale, projection
    ├── needs_vision, raster_path, text_char_count
    ├── title_block: TitleBlock{fields: {canonical_name: TitleBlockField}}
    ├── dimensions:  list[Dimension]  → kind, nominal, measured, tolerance, prefix,
    │                                   is_basic/is_reference/is_text_override, bbox
    ├── geometric_tolerances: list[GeometricTolerance] → characteristic, value,
    │                                   diametral_zone, material_condition, datums
    ├── datums, surface_finishes, welds, annotations, views
    └── features: list[GeometryFeature] → circle/arc/line/polyline (+ points)
```

Koordinatlar **sayfa uzayında** tutulur. DXF y-yukarı ve model uzayında
herhangi bir yerdedir; PDF/raster y-aşağıdır. Bu yüzden her `Sheet` kendi
`coordinate_space` ve `origin` değerini taşır; dönüşüm yalnızca bir kez,
çizim (overlay) sırasında yapılır.

Bulgu tarafı:

```
Finding(rule_id, severity, category, title/title_tr, message/message_tr,
        suggestion/suggestion_tr, standards, evidence=Evidence(sheet_index, bbox,
        object_ids, snippet), confidence, agent, id)
AuditReport(document, profile, findings, document_stats, agent_traces,
            overlays, warnings, duration_ms, extraction_failed)
```

Her bulgu iki dillidir (TR + EN); rapor dili çalışma zamanında seçilir.
`confidence` sezgisel kuralları dürüstçe işaretler (örn. ölçü–unsur eşleştirmesi
0.7, kapalı zincir sezgiseli 0.6).

## 4. Kural motoru / Rule engine

Bir kural = bir sınıf:

```python
@register
class UndimensionedFeatureRule(Rule):
    meta = RuleMeta(id="DIM001", title=…, title_tr=…, severity=Severity.MAJOR,
                    category=Category.DIMENSIONING,
                    standards=("ISO 129-1 §4.1", "ASME Y14.5-2018 §1.4"))
    scope = "sheet"          # veya "document"

    def check(self, target, ctx) -> Iterable[Finding]: ...
```

* `register` kayıt defterine ekler; `rules_for(config)` profil (ISO/ASME),
  kategori ve `--only/--disable` süzgeçlerini uygular.
* `RuleContext` paylaşılan türetilmiş bilgileri önbelleğe alır (genel tolerans
  notu var mı, profil ASME mi).
* `run_rules` her kuralı yalıtır: patlayan bir kural `INFO` bulgusuna dönüşür,
  denetimi durdurmaz.
* Yeni bir denetim eklemek = tek bir sınıf eklemek. Başka hiçbir dosya değişmez.

Sezgisel geometri yardımcıları `rules/analysis.py` içindedir: unsur–ölçü
eşleştirmesi, kapalı zincir tespiti (vektörel girdide ölçü aralıklarıyla
**kesin**, diğerlerinde küme sezgiseliyle), çakışan gösterim tespiti.

## 5. Çok modlu çıkarım / Multimodal extraction

`llm/` katmanı arka uçtan bağımsızdır:

* `VisionModel` (ABC) → `AnthropicVisionModel` (Claude), `MockVisionModel`
  (testler), `NullVisionModel` (`--vision off`).
* İstek biçimi: base64 görsel blokları + talimat, `thinking={"type":"adaptive"}`,
  `output_config={"format": {"type": "json_schema", …}, "effort": …}` ve
  sunucu taraflı red yedeklemesi (`server-side-fallback-2026-07-01`).
* Şema `llm/schemas.py` içindeki `VisionSheetExtraction`'dır; kutular görsele
  göre `0..1` normalize edilir, `llm/merge.py` bunları sayfa uzayına taşır ve
  metni **vektörel yolla aynı** dilbilgisinden geçirir.
* Büyük sayfalar `extract/raster.py` ile döşenir (tile); OpenCV varsa gürültü
  temizleme + eğrilik düzeltme uygulanır, yoksa adım atlanır.

## 6. Tasarım kararları / Design decisions

| Karar | Gerekçe |
| --- | --- |
| Vektör öncelikli, görsel yedekli | DXF/PDF ölçünün *gerçek* değerini taşır; sadece böyle "yazan 25, geometri 30" hatası yakalanabilir. |
| Tek metin dilbilgisi | DXF, PDF ve vision çıktısı aynı `text_parsing` üzerinden geçer; kurallar kaynağı bilmez. |
| İki dilli bulgular | Rapor sahaya Türkçe, denetim kaydına İngilizce gider. |
| Ajan başına hata kapsaması | Bir ajanın çökmesi "temiz rapor" üretmemeli. |
| `confidence` alanı | Sezgisel kural ile kesin kural aynı listede ama ayırt edilebilir. |
| İsteğe bağlı bağımlılıklar | Çekirdek yalnızca pydantic ister; ezdxf/PyMuPDF/Pillow/anthropic yoksa ilgili yol kapanır, program çalışır. |
