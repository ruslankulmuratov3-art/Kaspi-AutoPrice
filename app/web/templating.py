from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory='app/templates')


def money(value) -> str:
    try:
        return f'{float(value):,.0f}'.replace(',', ' ') + ' ₸'
    except Exception:
        return '0 ₸'


templates.env.filters['money'] = money
