# Eksik ölçülendirme tespiti — kısıt grafiği

> Bir resimde delik ⌀6.5 diye ölçülendirilmiş olabilir ama kenardan uzaklığı
> verilmemişse parça yapılamaz. Bu belge, uygulamanın "bu unsurun konumu
> türetilebilir mi?" sorusunu nasıl **kesin** olarak yanıtladığını anlatır.
>
> Kaynak: [`rules/constraints.py`](../src/catia_diff/rules/constraints.py) ·
> [`rules/views.py`](../src/catia_diff/rules/views.py)

---

## 1. Model: ölçülendirme bir grafiktir

Bir görünüşte, tek bir eksen boyunca:

- **Düğüm** = resmin ulaşabilmesi gereken koordinat (delik merkezi, kontur sınırı,
  eksen çizgisi)
- **Kenar** = o eksende ölçen bir ölçü (`[başlangıç, bitiş]` aralığı)

Tam ölçülendirilmiş bir görünüş, bu grafikte bir **kapsayan ağaçtır**:

| Grafik | Anlamı | Kural |
| --- | --- | --- |
| Bağlantılı, çevrimsiz | Tam ölçülendirilmiş | — |
| `bileşen − 1` kopukluk | O kadar **eksik ölçü** | `DIM011` / `DIM012` |
| `çevrim` sayısı | O kadar **fazla ölçü** | `DIM003` / `TOL010` |

Aynı yapı iki soruyu birden yanıtlar ve **sezgisel değildir**: kaynak her ölçünün
ölçtüğü aralığı veriyorsa (DXF veriyor), sonuç kesindir.

## 2. Örnek üzerinde adım adım

`examples/sample_plate.dxf` — 80×40 levha, dört ⌀6.5 delik:

```
Y
40 ┌──────────────────────────────┐
   │                              │
30 │   ○ (12,30)        ○ (68,30) │   ← 30 koordinatı: DIM0006 ile 0'a bağlı
   │                              │
10 │   ○ (12,10)        ○ (68,10) │   ← 10 koordinatı: HİÇBİR ÖLÇÜ ULAŞMIYOR
   │                              │
 0 └──────────────────────────────┘
   0      12            68      80
```

Uygulamanın kurduğu grafik:

```
X ekseni: düğümler {0, 12, 68, 80} · kenarlar 0-12, 12-68, 68-80, 0-80
          → 1 bileşen, 1 çevrim        → tam; ama bir ölçü fazla (DIM003)

Y ekseni: düğümler {0, 10, 30, 40} · kenarlar 0-40, 0-30
          → 2 bileşen, 0 çevrim        → 1 EKSİK ÖLÇÜ
          → serbest düğüm: 10 (FEAT0002, FEAT0004)
```

Üretilen bulgu:

```
MAJÖR DIM011 (12.0, 10.0), (68.0, 10.0) konumundaki ⌀6.5 unsuru Y ekseninde
             konumlandırılmamış: 10 koordinatına hiçbir ölçü ulaşmıyor.
```

`--complete` varyantı aynı levhayı kapsayan ağaç olacak şekilde ölçülendirir ve
**hiç** kapsam bulgusu üretmez:

```
X: 0-80 (toplam), 0-12, 68-80     → 4 düğüm, 3 kenar, çevrim yok
Y: 0-40 (toplam), 0-10, 30-40     → 4 düğüm, 3 kenar, çevrim yok
```

## 3. Görünüş ayrıştırma (ön koşul)

Ön görünüşün 80 mm'si yan görünüş hakkında hiçbir şey söylemez; bu yüzden grafik
**görünüş başına** kurulur. Resim bu gruplamayı kaydetmez, boşlukla ima eder:

1. Geometri kutuları, sayfanın büyük kenarının `view_gap_ratio` (varsayılan %6)
   kadarlık boşluğuna göre birleştirilir (ızgara + union-find, ~doğrusal)
2. Antet bölgesindeki tek nesnelik kümeler elenir
3. Her ölçü/sembol, erişim mesafesi içindeki en yakın görünüşe atanır → `view_id`
4. `KESİT A-A` gibi metin etiketleri, üzerinde durdukları görünüşe aktarılır

Ayrıştırma **çıkarım aşamasında bir kez** çalışır (`agents/extraction.py`), çünkü
denetleyici ajanlar paralel çalışır ve belgeyi yalnızca okur.

## 4. Düğüm seçimi — yanlış pozitif riskinin merkezi

Her poligon köşesini "ölçülmesi gereken koordinat" saymak, karmaşık konturlarda
gürültü üretir. Varsayılan olarak yalnızca şunlar düğümdür:

| Kaynak | Neden |
| --- | --- |
| Daire/yay merkezi | Konumlandırılması zorunlu |
| Konturun eksen üzerindeki uç sınırları | Parçanın sınırı |
| Eksene **dik** kenarlar | Tek bir koordinatta dururlar; ölçünün sabitlemesi gereken şey tam olarak budur |
| Eksen çizgileri (CENTER katmanı) | Simetriyle konumlandırma referansı |
| Ölçünün işaret ettiği ama geometride karşılığı bulunmayan koordinat | Gerçek bir referanstır, kenar kaybedilmez |

`--strict-dimensioning` her köşeyi düğüm yapar.

Yakın koordinatlar `node_merge_ratio` (görünüş boyunun binde biri) ile tek düğümde
birleşir.

## 5. Kurallar

| Kural | Önem | Ne der |
| --- | --- | --- |
| `DIM011` | Majör | Unsurun konumu belirlenmemiş — serbest düğümde bir delik/yay merkezi var |
| `DIM012` | Majör | Geometri ölçü zincirine bağlanmamış — serbest düğümde unsur yok, düz koordinat |
| `DIM013` | Kritik | Görünüşte hiç ölçü yok |
| `DIM014` | Minör | Toplam ölçü verilmemiş (zincir tam olsa bile) |
| `DIM015` | Minör | Eğik kenarın açısı verilmemiş |

## 6. Sessiz kalması gereken durumlar

Kurallar aşağıdaki hâllerde bulgu üretmez — her biri testlidir:

| Durum | Gerekçe |
| --- | --- |
| GD&T konum toleransı + teorik (basic) ölçü | Delik başka bir mekanizmayla konumlandırılmış |
| `4x ⌀6.5 EŞİT BÖLÜNMÜŞ` / `EQUALLY SPACED` | Patern notu konumu kapsıyor |
| `TÜM RADYÜSLER R3`, `ALL FILLETS R2` | Blanket not boyut gösterimini kapsıyor (`DIM001`) |
| Referans ölçü `(20)` | Bağlayıcı değil; kenar sayılmaz (ama eksik de saydırmaz) |
| Eğik kenar kendi ekseninde ölçülmüş | Açı zaten tanımlı |
| Kısa pah kenarları (`< %10` görünüş boyu) | Açı beklenmez |
| Antet köşesindeki tek nesne | Görünüş değil, antet çerçevesi |

## 7. Sınırlar

1. **Vektör-önce.** Analiz, her ölçünün ölçtüğü aralığı bilmeyi gerektirir. DXF bunu
   verir (`_measurement_interval`, ordinate ölçüler dâhil). PDF metin katmanında ve
   görsel çıkarımda bu veri yoktur; kurallar o zaman **hiç çalışmaz** (sessizce yanlış
   sonuç üretmek yerine). Sayfanın neden denetlenemediğini `CON005` bildirir.
2. **Çap/yarıçap/açı ölçüleri** boyutu kısıtlar, konumu değil; grafiğe girmezler.
   Boyut kapsamını `DIM001`/`DIM005` denetler.
3. **Simetri** yalnızca eksen çizgisi çizilmişse anlaşılır; "ortada" ima edilen ama
   çizilmemiş bir eksen serbest düğüm olarak görünür.
4. **Görünüşler arası ilişki** kurulmaz: ön görünüşte verilen bir ölçünün yan görünüşü
   de tanımladığı bilinmez. Aynı unsur iki görünüşte farklı ölçülendirilmişse
   yakalanmaz.

## 8. Deneme

```bash
# Eksik ölçüyü yakala (örnek resimde gerçek bir kusur var)
catia-diff audit examples/sample_plate.dxf --lang tr --only DIM011,DIM012

# Tam ölçülendirilmiş varyant: hiç bulgu üretmemeli
python examples/generate_sample_drawing.py examples/sample_plate_ok.dxf --complete
catia-diff audit examples/sample_plate_ok.dxf --only DIM011,DIM012,DIM013,DIM014,DIM015

# Katı mod: her kontur köşesi ölçülmüş olmalı
catia-diff audit cizim.dxf --lang tr   # (strict_dimensioning yapılandırmadan açılır)
```

Testler: [`tests/test_constraints.py`](../tests/test_constraints.py) (düğüm seçimi,
bileşen/çevrim sayımı, eğik eksen, tolerans),
[`tests/test_views.py`](../tests/test_views.py) (görünüş ayrıştırma) ve
[`tests/test_rules_coverage.py`](../tests/test_rules_coverage.py) (beş kural + sessiz
kalması gereken yedi durum).
