# Referans bütünlüğü — sarkan göndermeleri bulmak

> Bir resim, birbirini gösteren işaretlerin ağıdır. Kesit düzlemi bir görünüşü,
> not numarası bir notu, başlık geldiği işareti gösterir. Gösterilen şey yoksa
> okuyucu işi bitiremez: çizilmemiş bir görünüşe yollanır ya da hiç yazılmamış
> bir nota uymaya davet edilir.
>
> Kaynak: [`rules/references.py`](../src/catia_diff/rules/references.py) ·
> [`rules/markers.py`](../src/catia_diff/rules/markers.py)

---

## 1. Model: bildirim ve kullanım

Her referansın iki ucu vardır. Denetim tek soruyu sorar: **her kullanım tam
olarak bir bildirime çözülüyor mu?**

| Referans | Bildiren (declaration) | Kullanan (use) |
| --- | --- | --- |
| Kesit | kesit düzlemi + harf çifti (`A-A`) | `KESİT A-A` başlığı |
| Detay | detay balonu (daire + harf) | `DETAY B` başlığı |
| Not | numaralı not (`3. ÇAPAK ALINACAK`) | `BKZ NOT 3` |
| Sayfa | sayfanın varlığı | `MONTAJ İÇİN SAYFA 3` |
| Görünüş (metinden) | görünüş başlığı | `DETAY C'YE BAKINIZ` |

Çözümleme **belge genelindedir**: kesit bir sayfada alınıp başka sayfada
çizilebilir, not 1. sayfada yazılıp 2. sayfada anılabilir.

## 2. Başlık mı, gönderme mi?

Ayrım kuralın tamamını belirler. Çıkarıcı, içinde "DETAY" geçen **her** metni
görünüş olarak dosyalar — `DETAY C'YE BAKINIZ` dâhil. Gramer ikisini ayırır:

| Metin | `parse_view_caption` | `view_references` |
| --- | --- | --- |
| `KESİT A-A` | başlık (section, A-A) | — |
| `SECTION B-B SCALE 2:1` | başlık (section, B-B) | — |
| `DETAY C'YE BAKINIZ` | — | gönderme (detail, C) |
| `SEE DETAIL D FOR THE GROOVE` | — | gönderme (detail, D) |

Başlık harfinden sonra yalnızca ölçek gelebilir; başka bir kelime geliyorsa o
metin başlık değil, düzyazıdır. `KESİT A` ile `KESİT A-A` aynı referanstır.

## 3. İşaret tespiti — ve neden temkinli

| İşaret | Nasıl tanınır | Neden |
| --- | --- | --- |
| Kesit düzlemi | `A-A` harf çifti; yakınında parça-dışı katmanda çizgi varsa güven 1.0, yoksa 0.8 | Bir resimde `A-A` başka hiçbir amaçla yazılmaz |
| Detay balonu | **tek harf + parça-dışı katmanda daire** | Tek harf, datum sembolünün de yazılış biçimidir (`-A-`, `[A]`, `A`); ayrımı geometri yapar |

Çizim ofisleri kesiti çok farklı işaretler. Bu yüzden `REF002` **kapılıdır**:
belgede hiçbir işaret tanınamadıysa kural hiç çalışmaz. Aksi hâlde tespitin
tutmadığı her çizim stilinde tüm kesit başlıkları "işaretsiz" diye yanardı.
Aynı disiplin kapsam analizindeki `has_interval_data()` ile aynıdır:
*ölçemiyorsan konuşma.*

## 4. Kurallar

| Kural | Önem | Ne der |
| --- | --- | --- |
| `REF001` | **Kritik** | Kesit/detay işareti var, görünüşü belgede yok — okuyucu bilgiyi hiç bulamaz |
| `REF002` | Majör | Kesit/detay görünüşü var, nereden alındığı işaretlenmemiş |
| `REF003` | Majör | Aynı harf iki görünüşü adlandırıyor — gönderme çözülemez |
| `REF004` | Majör | Yazılmamış bir nota gönderme (`BKZ NOT 7`, notlar 1–3) |
| `REF005` | Majör | Var olmayan sayfaya gönderme |
| `REF006` | Majör | Metin çizilmemiş bir görünüşe gönderiyor (`DETAY C'YE BAKINIZ`) |
| `REF007` | Minör | Başlık hiçbir görünüşe oturmuyor — görünüş silinmiş, başlık kalmış |

`REF001` neden **Kritik**: kesit işareti, okuyucuya "bu kesitte göreceksin"
sözü verir. Görünüş yoksa o bilgi resimde hiç yoktur ve atölye tahmin eder.

### Neden "referans ölçü ≠ geometri" kuralı yok

Planda vardı; eklemeden önce sınadım. `(79)` yazan bir referans ölçü, geometri
80 ölçerken zaten `is_text_override=True` ile işaretleniyor ve **`DIM004`
Kritik olarak yakalıyor**. İkinci bir kural aynı kusuru iki kez raporlardı.

## 5. Sessiz kalması gereken durumlar

| Durum | Gerekçe |
| --- | --- |
| Belgede hiç işaret tanınamadı | `REF002` çalışmaz (madde 3) |
| Kesit başka sayfada çizilmiş | Çözümleme belge genelinde |
| Not başka sayfada yazılmış | Aynı |
| Antet `SAYFA 1 / 3` diyor, `SAYFA 3` göndermesi var | Takım üç sayfalıdır; dosya bir sayfa taşısa da gönderme geçerlidir |
| Yetim başlık | Yalnızca `REF007`; `REF002` aynı nesneyi ikinci kez raporlamaz |
| Tek harf, balonsuz | İşaret değil, datum sembolü olabilir |
| `KESİT A-A` metni başlıkken | Kendine gönderme sayılmaz |

## 6. Yan etki: kesit düzlemi ölçülendirilmez

Bu aile eklenirken kapsam analizinde bir kusur açığa çıktı: kesit düzlemi
çizgisi, **ölçülendirilmesi gereken bir koordinat** sayılıyordu. Kesit düzlemi
parçanın kenarı değildir; nerede kesildiğini söyler, ölçülendirilmez. Artık
"hayali" geometri (phantom, kesit, alternatif konum katmanları) ne düğüm üretir
ne de görünüşün sınırını genişletir — ikincisi olmasa hizalı iki görünüş,
aralarından geçen kesit çizgisi yüzünden "uyuşmuyor" görünürdü.

## 7. Deneme

```bash
# Kırık referanslar
python examples/generate_sample_drawing.py /tmp/refs.dxf --refs
catia-diff audit /tmp/refs.dxf --lang tr --category reference
#   KRITIK REF001  A-A kesit işareti var ancak 'KESİT A-A' görünüşü yok
#   MAJÖR  REF004  7 numaralı nota gönderme, böyle bir not yok
#   MAJÖR  REF005  3. sayfaya gönderme, resimde 1 sayfa var
#   MAJÖR  REF006  'detay C' göndermesi, böyle bir görünüş yok
#   MINÖR  REF007  'KESİT B-B' başlığı hiçbir geometriye ait değil

# Hepsi düzeltilmiş hâli: sessiz kalmalı
python examples/generate_sample_drawing.py /tmp/refs_ok.dxf --refs --complete
catia-diff audit /tmp/refs_ok.dxf --lang tr --category reference
#   Bulgu yok
```

Testler: [`tests/test_references.py`](../tests/test_references.py) — gramer,
işaret tespiti, yedi kural ve sessiz kalması gereken sekiz durum.
