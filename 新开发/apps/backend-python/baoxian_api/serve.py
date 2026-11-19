import os

import uvicorn


def serve(env):
    uvicorn.run('baoxian_api.main:create_application', factory=True,
                host=env.get('API_HOST', '').strip() or '127.0.0.1',
                port=int(env.get('API_PORT', '').strip() or '3001'))


if __name__ == '__main__':
    serve(os.environ)
