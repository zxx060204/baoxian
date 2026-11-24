import os

import uvicorn


def serve(env):
    uvicorn.run('baoxian_api.benefit_api:create_application', factory=True,
                host=env.get('BENEFIT_API_HOST', '').strip() or env.get('API_HOST', '').strip() or '127.0.0.1',
                port=int(env.get('BENEFIT_API_PORT', '').strip() or '3002'))


if __name__ == '__main__':
    serve(os.environ)
