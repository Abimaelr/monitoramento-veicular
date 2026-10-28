from camaras.sources import _parece_camera, descobrir_midia, rotulos_midia

HTML = """
<html><body>
<img src="/img/logo.png">
<img src="/camaras/plaza.jpg">
<iframe src="https://cams.example.cl/embed/42"></iframe>
<video><source src="//stream.example.cl/live/cam1.m3u8"></video>
<script>var s = "http://10.0.0.5/mjpg/video.mjpg";</script>
</body></html>
"""


def test_descobrir_midia():
    urls = descobrir_midia(HTML, "http://www.portalimo.cl/camaras/")
    assert "http://www.portalimo.cl/camaras/plaza.jpg" in urls
    assert "http://stream.example.cl/live/cam1.m3u8" in urls
    assert "http://10.0.0.5/mjpg/video.mjpg" in urls
    cams = [u for u in urls if _parece_camera(u)]
    assert "http://www.portalimo.cl/img/logo.png" not in cams


def test_rotulos_usam_legenda_do_cartao():
    html = """<div class="cam-card" onclick="modalClick('http://1.2.3.4:19021/player.html', 'X')">
      <span class="badge">EN VIVO</span>
      <img class="cam-thumb" src="http://1.2.3.4:19021/images/live.jpg" alt="Plaza de Armas">
      <p class="cam-caption">Plaza de Armas - Pileta</p></div>
      <div><img src="/c2.jpg" alt="Puente San Pablo"></div>"""
    nomes = rotulos_midia(html, "http://www.portalimo.cl/")
    assert nomes["http://1.2.3.4:19021/images/live.jpg"] == "Plaza de Armas - Pileta"
    assert nomes["http://www.portalimo.cl/c2.jpg"] == "Puente San Pablo"
