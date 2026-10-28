from camaras.sources import _parece_camera, descobrir_midia

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
