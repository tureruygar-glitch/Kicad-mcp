# kicad10-mcp

Bir **MCP sunucusu** — KiCad 10 üzerinde **tam kontrol** sağlar. KiCad'in IPC
API'sini (`kicad-python` / `kipy`) ve `kicad-cli`'yi sararak PCB editörü,
şematik editörü, proje ayarları, ağlar (nets), katmanlar, tasarım verisi,
üretim çıktıları ve ham bir betik çalıştırma kapısını MCP araçları olarak sunar.

82 araç, 9 modülde gruplanmıştır. İngilizce araç adları ve açıklamaları
modelin doğru aracı bulması için tutulmuştur.

## Gereksinimler

- **KiCad 10** kurulu (bu makinede: `C:\Program Files\KiCad\10.0`, kicad-cli 10.0.3)
- Python paketleri (zaten kurulu): `kicad-python>=0.7.1`, `mcp>=1.10`
- KiCad açık ve **API sunucusu etkin**:
  `Preferences > Plugins > "Enable the KiCad API server"`
- Çoğu araç **o anda AÇIK olan** kart (`.kicad_pcb`) veya şema (`.kicad_sch`)
  üzerinde çalışır — önce ilgili dökümanı açın.

## Çalıştırma

```powershell
python -m kicad10_mcp        # veya: python run_server.py
```

Sunucu stdio üzerinden konuşur. Bu proje, Claude Code'un global MCP yapılandırmasında
`kicad` adıyla zaten kayıtlıdır (`~/.claude.json`), şu komutu kullanır:

```
C:\Users\UYGAR\AppData\Local\Python\pythoncore-3.14-64\python.exe  run_server.py
```

> Eski sınırlı sunucu (`C:\Users\UYGAR\kicad-mcp\.venv\Scripts\kicad-mcp.exe`)
> bu kayıtla değiştirilmiştir. Geri dönmek isterseniz `~/.claude.json` içindeki
> `mcpServers.kicad.command/args` alanlarını eski değerine çevirin.

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

**Oluşturma (routing/grafik)** — `add_track`, `add_arc_track`, `add_via`,
`add_zone`, `add_zone_rect`, `refill_zones`, `add_line`, `add_rectangle`,
`add_circle`, `add_arc`, `add_polygon`, `add_board_outline_rect`, `add_text`

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

## Proje yapısı

```
kicad10_mcp/
  server.py          FastMCP uygulaması, tüm modülleri kaydeder
  connection.py      Önbellekli KiCad istemcisi + commit context manager
  helpers.py         mm<->nm, katman ad<->enum, serileştiriciler
  system_tools.py    sistem / döküman yaşam döngüsü
  read_tools.py      PCB okuma
  edit_tools.py      PCB düzenleme
  create_tools.py    routing + grafik + metin oluşturma
  net_layer_tools.py ağlar, ağ sınıfları, katmanlar, stackup, tasarım kuralları
  project_tools.py   metin değişkenleri, başlık bloğu
  schematic_tools.py şema okuma/yazma
  export_tools.py    kicad-cli sarmalayıcıları
  exec_tools.py      execute_kipy
run_server.py        başlatıcı
pyproject.toml
```
