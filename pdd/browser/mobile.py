"""手机站设备配置：使用 Chromium 标准设备模拟，不替换网站参数生成逻辑。"""


def configure_mobile_page(page, *, edge=True):
    page.set_viewport_size({"width": 412, "height": 839})
    session = page.context.new_cdp_session(page)
    version = session.send("Browser.getVersion")["product"].split("/")[-1]
    major = version.split(".")[0]
    user_agent = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  f"(KHTML, like Gecko) Chrome/{version} Mobile Safari/537.36")
    brands = [{"brand": "Chromium", "version": major}]
    if edge:
        user_agent += f" EdgA/{version}"
        brands.append({"brand": "Microsoft Edge", "version": major})
    else:
        brands.append({"brand": "Google Chrome", "version": major})
    session.send("Emulation.setUserAgentOverride", {
        "userAgent": user_agent, "platform": "Linux armv8l",
        "userAgentMetadata": {"brands": brands, "fullVersion": version,
                              "platform": "Android", "platformVersion": "10.0.0",
                              "architecture": "arm", "model": "K", "mobile": True},
    })
    session.send("Emulation.setDeviceMetricsOverride", {
        "width": 412, "height": 839, "deviceScaleFactor": 2.625, "mobile": True,
        "screenWidth": 412, "screenHeight": 915,
    })
    session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
    page._pdd_touch_session = session
    page._pdd_edge = edge
    return session


def manual_pointer(page, points, active):
    """把用户实际输入的位置和间隔送给手机页面；不生成或猜测验证轨迹。"""
    size = page.viewport_size or page.evaluate("({width:innerWidth,height:innerHeight})")
    touch = getattr(page, "_pdd_touch_session", None)
    def coords(point):
        return {"x": point[0] * (size["width"] - 1), "y": point[1] * (size["height"] - 1)}
    def dispatch(kind, positions):
        touch.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": positions})
    if not active():
        return
    if touch:
        dispatch("touchStart", [coords(points[0])])
    else:
        page.mouse.move(**coords(points[0]))
        page.mouse.down()
    completed = False
    try:
        for point in points[1:]:
            if not active():
                return
            page.wait_for_timeout(point[2])
            if not active():
                return
            if touch:
                dispatch("touchMove", [coords(point)])
            else:
                page.mouse.move(**coords(point))
        completed = active()
    finally:
        if touch:
            dispatch("touchEnd" if completed else "touchCancel", [])
        else:
            page.mouse.up()


def scroll_input(page, x, y, distance, viewport_height):
    """distance 为正时向下浏览；手机发送触屏滑动，桌面保留滚轮。"""
    session = getattr(page, "_pdd_touch_session", None)
    if session is None:
        page.mouse.move(x, y)
        page.mouse.wheel(0, distance)
        return
    available = y - 12 if distance > 0 else viewport_height - y - 12
    amount = min(abs(distance), max(0, available))
    if amount <= 0:
        return
    session.send("Input.synthesizeScrollGesture", {
        "x": x, "y": y, "yDistance": -amount if distance > 0 else amount,
        "gestureSourceType": "touch", "speed": 500, "preventFling": True,
    })
