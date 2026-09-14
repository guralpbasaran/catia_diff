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
KRITIK DIM011 (12.0, 10.0), (68.0, 10.0) konumundaki ⌀6.5 unsuru Y ekseninde
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
| `DIM011` | **Kritik** | Unsurun konumu belirlenmemiş — serbest düğümde bir delik/yay merkezi var |
| `DIM012` | Majör | Geometri ölçü zincirine bağlanmamış — serbest düğümde unsur yok, düz koordinat |
| `DIM013` | Kritik | Görünüşte hiç ölçü yok |
| `DIM014` | Minör | Toplam ölçü verilmemiş (zincir tam olsa bile) |
| `DIM015` | Minör | Eğik kenarın açısı verilmemiş |
| `DIM003` | Majör | **Fazla ölçü** — grafikte çevrim; kapatan kenar fazla olan ölçüdür |
| `TOL010` | Majör | Çevrimdeki tolerans birikimi toplam ölçüye sığmıyor |

`DIM011` neden **Kritik**: konumu türetilemeyen bir delik, çapı doğru verilmiş olsa
bile parçayı imal edilemez kılar — atölye deliği nereye açacağını bilemez. Bu, "eksik
bilgi" değil "yanlış parça" sınıfına giren bir kusurdur.

`DIM003` ve `TOL010` aynı motoru kullanır: bir kenar, uçları zaten bağlı olan iki
düğümü birleştiriyorsa çevrim kapanır ve **o kenar fazladır**. Grafik çevrimin
hangi ölçülerden geçtiğini de verdiği için bulgu, fazla olan ölçüyü adıyla söyler:

```
X ekseninde 80.00 ölçüsü fazla: 12.00 + 56.00 + 12.00 zaten aynı mesafeyi belirliyor.
```

İki uzunluğundaki çevrim, aynı aralığın ikinci kez ölçülmesidir ve ayrı ifade edilir:

```
Y ekseninde 40.00 ölçüsü DIM0010 ile aynı mesafeyi ölçüyor; ölçü tekrar edilmiş.
```

Bu ikinci durum, eski bitişik-zincir sezgiselinin **göremediği** bir kusurdu: o yol
"küçük ölçülerin toplamı büyük ölçüye eşit mi" diye bakıyordu, iki uzunluğunda
çevrim ise toplam içermez.

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
   görsel çıkarımda bu veri yoktur; **eksik** ölçü kuralları o zaman hiç çalışmaz
   (sessizce yanlış sonuç üretmek yerine) ve `CON005` durumu bildirir.
   `DIM003`/`TOL010` ise bu kaynaklarda daha zayıf bir yola düşer: aynı hizadaki
   gösterimlerin toplamını karşılaştırır ve bulguyu `%60` güvenle işaretler.
2. **Çap/yarıçap/açı ölçüleri** boyutu kısıtlar, konumu değil; grafiğe girmezler.
   Boyut kapsamını `DIM001`/`DIM005` denetler.
3. **Simetri** yalnızca eksen çizgisi çizilmişse anlaşılır; "ortada" ima edilen ama
   çizilmemiş bir eksen serbest düğüm olarak görünür.
4. **Görünüşler arası eşleme unsur düzeyinde yapılmaz.** Hizalı görünüşlerin ortak
   *toplam* uzunluğu karşılaştırılır (bölüm 7b); hangi deliğin hangi görünüşte
   hangisine karşılık geldiği çözülmez. Hizasız yerleştirilmiş veya `%10`'dan fazla
   ayrışan görünüş çiftleri hiç karşılaştırılmaz.

## 7b. Görünüşler arası: hizalama

Tek görünüşün grafiği, o görünüşün dışını göremez. Ortografik yerleşim bir gerçek
verir: **üst görünüş ön görünüşün genişliğini, yan görünüş yüksekliğini paylaşır.**
İki görünüşün sınır kutuları bir eksende örtüşüp diğerinde ayrıksa o eksen ortaktır
(`rules/projection.py`).

Bundan iki şey çıkar. Birincisi bir **yanlış pozitifi kapatır**:

```
Ön görünüş   X: [0, 12, 68, 80]  3 ölçü → tam
Üst görünüş  X: [0, 12, 68, 80]  0 ölçü → "3 ölçü eksik"   ← yanlış
                                          X ön görünüşte verilmiş
```

Ortografik uygulama bir ölçüyü tek görünüşte verir. Kapsam kuralları artık hizalı
görünüşten **miras** alır (`DIM011`, `DIM012`, `DIM014`). Miras yalnızca komşu
görünüş o ekseni **tam** kısıtlıyorsa geçerlidir; kendisi eksik olan bir görünüş
hiçbir şey devretmez, böylece gerçek bir boşluk iki görünüş arasında kaybolmaz.

İkincisi yeni bir kusur sınıfı açar:

| Kural | Önem | Ne der |
| --- | --- | --- |
| `CRV001` | Kritik | İki görünüş ortak uzunluğu farklı ölçülendirmiş (80 ve 76) |
| `CRV002` | Majör | Ölçüler değil **geometri** ayrışmış — biri güncellenip diğeri unutulmuş |
| `CRV003` | Minör | Aynı uzunluk iki görünüşte de ölçülendirilmiş (ISO 129-1 §4.3) |

Bantlar kararı verir: fark `%2`'nin altındaysa görünüşler aynı, `%2–%10` arasındaysa
aynı olması gerekirken ayrışmış (rapor), `%10`'un üstündeyse **farklı şeyler**
(detay, kopuk görünüş, başka ölçek) — ne karşılaştırılır ne miras alınır. Detay
görünüşleri baştan dışarıda.

Bir yan etki: merkez çizgisinin *boyu* artık düğüm üretmiyor. Eksen çizgisi bir
konumu bildirir; parçadan ne kadar taştığı çizim üslubudur, ölçülendirilmesi
gereken bir koordinat değil.

## 8. Deneme

```bash
# Eksik ölçüyü yakala (örnek resimde gerçek bir kusur var)
catia-diff audit examples/sample_plate.dxf --lang tr --only DIM011,DIM012

# Görünüşler arası: çelişkili üç görünüş, sonra doğru varyantı (sessiz kalmalı)
python examples/generate_sample_drawing.py /tmp/views.dxf --views
catia-diff audit /tmp/views.dxf --lang tr --only CRV001,CRV002,CRV003
python examples/generate_sample_drawing.py /tmp/views_ok.dxf --views --complete
catia-diff audit /tmp/views_ok.dxf --lang tr --category cross_view,dimensioning

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
