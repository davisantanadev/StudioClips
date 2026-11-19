from clipador.reframe import Framing, avoid_webcam
from clipador.render import H, W, layout
from clipador.webcam import head_and_shoulders, persistent_face


def _face(x, y, s=70):
    return (x, y, s * 0.8, s, 0.9)


def test_streamer_face_beats_moving_game_faces():
    dets = []
    for i in range(12):
        f = [_face(1060, 228)]                    # webcam: parada no canto
        if i % 3 == 0:
            f.append(_face(200 + i * 60, 160))    # personagem do jogo: anda pela tela
        dets.append(f)
    face, main_h = persistent_face(dets, 12)
    assert abs(face[0] - 1060) < 5 and abs(face[1] - 228) < 5 and main_h == 70


def test_no_webcam_when_no_face_stays_put():
    dets = [[_face(100 + i * 90, 300)] for i in range(12)]   # só rostos que se mexem
    assert persistent_face(dets, 12) is None


def test_two_people_in_one_cam_are_joined():
    dets = [[_face(100, 60), _face(170, 64)] for _ in range(12)]
    (x, y, w, h), _ = persistent_face(dets, 12)
    assert x <= 100 and x + w >= 170 + 56


def test_head_and_shoulders_stays_inside_frame():
    l, t, r, b = head_and_shoulders((1220, 680, 50, 40), 40)   # rosto colado no canto
    assert 0 <= l < r <= 1280 and 0 <= t < b <= 720


def test_layout_less_zoom_and_cam_on_top():
    cam = (994, 186, 242, 170)
    aberto, fechado = layout(cam, "aberto"), layout(cam, "fechado")
    assert aberto.cam_y < aberto.game_y == aberto.cam_y + aberto.cam_h     # webcam em cima, jogo logo abaixo
    assert aberto.game_h < fechado.game_h                                  # aberto = menos zoom no jogo
    assert fechado.cam_h + fechado.game_h == H and not fechado.padded
    assert aberto.padded and aberto.game_y + aberto.game_h <= H
    assert aberto.game_y < aberto.sub_y < aberto.game_y + aberto.game_h   # legenda em cima do jogo
    nocam = layout(None, "fechado")
    assert nocam.cam_h == 0 and nocam.game_h == H


def test_game_crop_avoids_webcam_side():
    fr = Framing((0, 0, 1280, 720), [(0.0, 640.0)])
    right = avoid_webcam(fr, (994, 186, 242, 170), cw=884)
    assert right.box == (0, 0, 994, 720)                  # câmera à direita: jogo vai até antes dela
    left = avoid_webcam(fr, (39, 386, 204, 162), cw=884)
    assert left.box == (243, 0, 1037, 720)
    assert avoid_webcam(fr, (500, 300, 300, 200), cw=884).box == fr.box   # não cabe: deixa como está
    assert W == 1080
