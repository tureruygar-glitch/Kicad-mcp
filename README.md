# kicad10-mcp

Bir **MCP sunucusu** — KiCad 10 üzerinde **tam kontrol** sağlar. KiCad'in IPC
API'sini (`kicad-python` / `kipy`) ve `kicad-cli`'yi sararak PCB editörü,
şematik editörü, proje ayarları, ağlar (nets), katmanlar, tasarım verisi,
üretim çıktıları ve ham bir betik çalıştırma kapısını MCP araçları olarak sunar.

102 araç, 14 modülde gruplanmıştır. İngilizce araç adları ve açıklamaları
modelin doğru aracı bulması için tutulmuştur.

## Gereksinimler

- **KiCad 10** kurulu (bu makinede: `C:\Program Files\KiCad\10.0`, kicad-cli 10.0.5)
- Python paketleri: `kicad-python>=0.7.1`, `mcp>=1.10,<2`, `pillow>=10`

  ```powershell
  pip install -e .
  ```

  > `mcp` 2.x'te `FastMCP` kaldırıldı; sunucu 1.x API'siyle yazıldığı için sürüm `<2`
  > ile sabitlenmiştir.
- KiCad açık ve **API sunucusu etkin**:
  `Preferences > Plugins > "Enable the KiCad API server"`
- Çoğu araç **o anda AÇIK olan** kart (`.kicad_pcb`) veya şema (`.kicad_sch`)
  üzerinde çalışır — önce ilgili dökümanı açın.

## Çalıştırma

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe run_server.py      # veya: python -m kicad10_mcp
```

Sunucu stdio üzerinden konuşur. Claude Code'a kaydetmek için:

```powershell
claude mcp add kicad --scope user -- <repo>\.venv\Scripts\python.exe <repo>\run_server.py
```

`claude` komutu yoksa (yalnızca masaüstü uygulaması kuruluysa) aynı kaydı
`~/.claude.json` içine `mcpServers.kicad` olarak elle ekleyin:

```json
"mcpServers": {
  "kicad": {
    "type": "stdio",
    "command": "<repo>\\.venv\\Scripts\\python.exe",
    "args": ["<repo>\\run_server.py"]
  }
}
```

Kayıttan sonra Claude Code oturumunu yeniden başlatın. Test: `kicad_status`
aracı "no handler available" derse KiCad'e bağlanılmıştır ama PCB Editor açık
değildir.

## Birimler / kurallar

- Tüm konum, boyut ve genişlikler **milimetre**; açılar **derece**.
- Katman adları: `F.Cu`, `B.Cu`, `In1.Cu`, `Edge.Cuts`, `F.SilkS`, `F.Mask`,
  `F.Paste`, `F.Fab` … Geçerli set için `list_board_layers`.
- Kart düzenlemeleri tek bir geri-al (undo) adımına gruplanır. Diske yazmak için
  `save_board`. Export araçları aksi belirtilmedikçe önce kaydeder.

## Araç grupları

**Sistem / bağlantı** — `kicad_status`, `get_version`, `ping`,
`list_open_documents`, `save_board`, `save_board_as`, `revert_board`,
`run_action`, `get_kicad_binary_path`

**PCB okuma** — `get_board_summary`, `list_footprints`, `get_footprint`,
`list_pads`, `list_tracks`, `list_vias`, `list_zones`, `list_shapes`,
`get_board_outline`, `list_text`, `list_dimensions`, `list_groups`,
`get_bounding_box`

**PCB düzenleme** — `move_footprint`, `rotate_footprint`,
`set_footprint_locked`, `set_footprint_value`, `batch_move_footprints`,
`set_items_locked`, `delete_items`, `select_items`, `clear_selection`,
`get_selection`

**Yerleşim (parça/pad/kenar bazlı)** — `get_footprint_geometry`,
`place_near_pad` (dekuplaj vb.; aynı netteki pad hedefe bakacak şekilde gerekirse
180° döndürür), `place_relative`, `place_on_edge`, `arrange_row`,
`flip_footprint`, `check_placement` (courtyard çakışması, kart dışı parça,
toplam airwire uzunluğu). `move_footprint` / `batch_move_footprints` da artık
çakışma uyarısı döndürür.

**Routing (pad bazlı)** — `route_pads` (iki pad'i adıyla bağlar: net ve genişlik
netclass'tan, 45°/90° yol, gerekirse via), `add_track_path` (çok parçalı yol),
`check_clearance`. Her sonuçta `warnings` alanı kısa devre, clearance ihlali ve
boşta kalan uçları bildirir; `rollback_on_conflict` ile hatalı çizim geri alınır.

**Netclass / track genişliği** — `calc_track_width` (akıma göre IPC-2221 genişliği,
 istenirse direnç ve gerilim düşümü), `configure_netclasses` (sınıfları ve net atamalarını
`.kicad_pro`'ya yazar; genişlik sabit sayı yerine `current_a` ile verilebilir; proje
KiCad'de kapalıyken çalışır), `get_netclass_config`.

**Otomatik routing (Freerouting)** — `autoroute`: kalan bağlantıları
[Freerouting](https://github.com/freerouting/freerouting) ile çizer. Mevcut yollar
kilitlenir (elle/Claude'un çizdiği güç yolları yerinde kalır), `skip_netclasses`
ile istenen sınıflar (ör. `HighCurrent`) hiç rotalanmaz. Açık kart kaydedilir,
`<kart>.pre-autoroute.kicad_pcb` yedeği alınır, rotalanıp KiCad'e yeniden
yüklenir; ardından zone'lar doldurulup DRC çalıştırılır (`drc_after`).
`freerouting_status` hazırlığı kontrol eder, `install_freerouting` resmi jar'ı
GitHub'dan indirir. Freerouting GPL-3.0'dır ve yalnızca ayrı bir program olarak
çağrılır; kodu bu pakete dahil değildir. Gereksinim: Java 21+ (Java 25 önerilir:
Freerouting 2.4 saniyeler içinde biter ve limitlere uyar; Java 21'de 2.1.0 çalışır,
limitleri yok sayar ve dakikalar sürer). DSN/SES dönüşümü KiCad'in kendi Python'u
(`pcbnew`) ile yapılır, çünkü `kicad-cli` DSN dışa aktaramaz. Not: bakır
katmandaki yazılar Freerouting'e engel olarak gitmez; DRC raporunu okuyun.

**Güç bütçesi (akım analizi)** — `analyze_power_budget`: şematikten (kicad-cli
netlist; KiCad açık olmak zorunda değil) her besleme netinin normal ve en kötü
durum akımını hesaplar; regülatör, sürücü ve seri elemanlar (sigorta, diyot,
anahtar, bobin, 0 Ω) üzerinden akımı kaynağa kadar taşır, sürücü/regülatör
sınır aşımlarını uyarır ve `configure_netclasses`'a verilecek netclass önerisi
üretir. Bilinmeyen parçaları ve ucunda ne olduğu bilinmeyen konnektörleri tahmin
etmez, soru olarak döndürür. `set_part_current` datasheet değerlerini kaynağıyla
kaydeder, `list_part_database` bilinen parçaları listeler.

Parça verisi sırası: kullanıcının kendi kayıtları (`~/.kicad10_mcp/parts.json`)
→ yerleşik veritabanı. Yerleşik 16 parçanın 13'ü üretici datasheet'lerinden
alınmıştır (her kayıtta kaynak URL ve sayfa); datasheet'i alınamayan MCP1700,
L7805 ve WS2812B `verified: false` olarak işaretlidir.

**Görünüm** — `snapshot_board`: kartın üstten PNG görüntüsü (kart sınırı,
courtyard'lar, pad'ler, track/via'lar, airwire'lar, vurgulanan net). Model
yerleşimi ve routing'i görerek kontrol edebilir.

**Oluşturma (routing/grafik)** — `add_track`, `add_arc_track`, `add_via`,
`add_zone`, `add_zone_rect`, `refill_zones`, `add_line`, `add_rectangle`,
`add_circle`, `add_arc`, `add_polygon`, `add_board_outline_rect`, `add_text`
(`add_track` genişliği ve `add_via` ölçüleri verilmezse netclass'tan alınır;
bilinmeyen net adı artık hata verir ve benzer adları önerir.)

**Ağlar / katmanlar** — `list_nets`, `list_netclasses`, `get_items_by_net`,
`get_connected_items`, `list_board_layers`, `set_active_layer`,
`set_visible_layers`, `set_copper_layer_count`, `get_stackup`,
`get_design_rules`

**Proje** — `get_project_info`, `get_text_variables`, `set_text_variable`,
`expand_text`, `get_title_block`, `set_title_block`

**Şema** — `get_schematic_summary`, `list_symbols`, `list_labels`,
`list_schematic_text`, `get_schematic_hierarchy`, `add_schematic_text`,
`add_local_label`, `save_schematic`
(Not: sembol/hiyerarşi okuma KiCad 11 özelliğidir; KiCad 10'da bu araçlar
açıklayıcı bir hata döndürebilir — bu durumda `execute_kipy` kullanın.)

**Üretim çıktıları (kicad-cli)** — `run_kicad_cli`, `export_gerbers`,
`export_drill`, `export_step`, `export_pdf`, `export_svg`, `export_pos`,
`render_3d`, `run_drc`, `export_bom`, `export_netlist`, `run_erc`

**Tam kontrol kapısı** — `execute_kipy`: canlı KiCad'e karşı rastgele Python
çalıştırır. `kicad`, `board`, `schematic`, `kipy`, `commit`, `Vector2`, `Angle`,
`BoardLayer`, `KiCadObjectType` adları önceden bağlıdır. `result` değişkenine
atadığınız şey geri döner; `print` çıktısı da yakalanır.

```python
# execute_kipy örnek
from kipy.board_types import Track
t = Track()
t.start = Vector2.from_xy_mm(10, 10)
t.end   = Vector2.from_xy_mm(20, 10)
t.width = 250000          # 0.25 mm (nanometre)
t.layer = BoardLayer.BL_F_Cu
with commit(board, "api track"):
    created = board.create_items(t)
result = [c.id.value for c in created]
```

## Ortam değişkenleri

- `KICAD_API_TIMEOUT_MS` — IPC istek zaman aşımı (varsayılan 10000)
- `KICAD_API_SOCKET` / `KICAD_API_TOKEN` — KiCad otomatik ayarlar; genelde gerekmez
- `KICAD10_MCP_JAVA` — Freerouting için kullanılacak `java` (varsayılan: bulunan en yeni sürüm)
- `KICAD10_MCP_FREEROUTING_JAR` — belirli bir Freerouting jar dosyası
- `KICAD10_MCP_KICAD_PYTHON` — KiCad'in Python'u (DSN/SES dönüşümü için; genelde otomatik bulunur)

## Proje yapısı

```
kicad10_mcp/
  server.py          FastMCP uygulaması, tüm modülleri kaydeder
  connection.py      Önbellekli KiCad istemcisi + commit context manager
  helpers.py         mm<->nm, katman ad<->enum, serileştiriciler
  board_query.py     parça/pad bulma, courtyard, netclass değerleri, geometri, ratsnest
  system_tools.py    sistem / döküman yaşam döngüsü
  read_tools.py      PCB okuma
  edit_tools.py      PCB düzenleme
  placement_tools.py parça/pad/kenar bazlı yerleşim + yerleşim kontrolü
  routing_tools.py   pad bazlı routing + clearance / bağlantı kontrolü
  view_tools.py      snapshot_board (PNG görüntü)
  netclass_tools.py  IPC-2221 hesabı + .kicad_pro netclass/atama düzenleme
  power_tools.py     güç bütçesi analizi (netlist → net akımları → netclass önerisi)
  parts_db.py        parça akım veritabanı (yerleşik + kullanıcı)
  sexpr.py           KiCad S-expression okuyucu/yazıcı
  autoroute_tools.py Freerouting entegrasyonu (DSN → Freerouting → SES)
  kicad_py/          KiCad'in Python'u ile çalışan yardımcılar (DSN/SES)
  create_tools.py    routing + grafik + metin oluşturma
  net_layer_tools.py ağlar, ağ sınıfları, katmanlar, stackup, tasarım kuralları
  project_tools.py   metin değişkenleri, başlık bloğu
  schematic_tools.py şema okuma/yazma
  export_tools.py    kicad-cli sarmalayıcıları
  exec_tools.py      execute_kipy
run_server.py        başlatıcı
pyproject.toml
```
