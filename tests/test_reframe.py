from clipador.reframe import Framing, crop_geometry, crop_x_expr, plan

W, H = 160, 90


def _frames(n, block_x0, block_x1, static_cols=0, cam=None):
    """Frames cinza: colunas [0, static_cols) paradas (painel de chat), um bloco claro andando de
    block_x0 até block_x1 e textura que muda no resto (gameplay)."""
    out = []
    for t in range(n):
        bx = int(block_x0 + (block_x1 - block_x0) * t / max(n - 1, 1))
        row = bytearray(W)
        for x in range(W):
            if x < static_cols:
                row[x] = 60
            else:
                row[x] = (x * 7 + t * 13) % 40 + 20  # gameplay "mexendo" de leve
            if bx <= x < bx + 14:
                row[x] = 250 if t % 2 else 5        # ação forte no bloco
        f = bytearray()
        for y in range(H):
            if cam and cam[1] <= y < cam[1] + cam[3]:
                r = bytearray(row)
                for x in range(cam[0], cam[0] + cam[2]):
                    r[x] = 255 if t % 2 else 0      # webcam piscando: não pode puxar o enquadramento
                f += r
            else:
                f += row
        out.append(bytes(f))
    return out


def test_static_side_panel_is_left_out_of_gameplay_box():
    fr = plan(_frames(30, 100, 120, static_cols=48), W, H, fps=2)
    x, y, w, h = fr.box
    assert 48 * 8 - 16 <= x <= 48 * 8 + 16      # caixa começa depois do painel (em 1280x720)
    assert x + w >= 1260 and h >= 700


def test_center_follows_the_action():
    fr = plan(_frames(40, 20, 130), W, H, fps=2)
    first, last = fr.x_at(0), fr.x_at(fr.keys[-1][0])
    assert last - first > 250                    # andou pra direita junto com a ação
    assert all(abs(b[1] - a[1]) <= 140 * (b[0] - a[0]) + 1 for a, b in zip(fr.keys, fr.keys[1:]))  # sem trancos


def test_webcam_area_is_ignored():
    cam = (0, 0, 40, 30)                          # canto superior esquerdo, em 160x90
    fr = plan(_frames(30, 110, 112, cam=cam), W, H, fps=2, webcam=(0, 0, 320, 240))
    assert fr.x_at(5) > 700                       # foi atrás do bloco à direita, não da webcam


def test_frozen_video_falls_back_to_center():
    f = bytes([80]) * (W * H)
    fr = plan([f] * 20, W, H, fps=2)
    assert fr.box == (0, 0, 1280, 720) and fr.x_at(3) == 640


def test_crop_geometry_and_expression():
    fr = Framing(box=(0, 0, 1280, 720), keys=[(0.0, 300.0), (2.0, 1200.0)])
    cw, ch, cy = crop_geometry(fr, 1080 / 1920)
    assert (cw, ch, cy) == (398, 708, 6)          # apara 6 px em cima/embaixo; largura par (yuv420)
    expr = crop_x_expr(fr, cw)
    assert "between" not in expr and "t" in expr  # expressão por tempo, sem nada exótico
    assert crop_x_expr(Framing((0, 0, 1280, 720), [(0.0, 640.0)]), cw) == "441"  # parado: número fixo
    assert Framing.from_json(fr.to_json()) == fr
