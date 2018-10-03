import functools

import pytest
from trio_websocket import ConnectionClosed, connect_websocket, \
    connect_websocket_url, open_websocket, open_websocket_url, \
    serve_websocket, ListenPort, WebSocketServer
import trio
import trio.hazmat
import trio.ssl
import trustme


HOST = 'localhost'
RESOURCE = '/resource'

@pytest.fixture
async def echo_server(nursery):
    ''' A server that reads one message, sends back the same message,
    then closes the connection. '''
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0, None)
    yield server


@pytest.fixture
async def echo_conn(echo_server):
    ''' Return a client connection instance that is connected to an echo
    server. '''
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        yield conn


async def echo_handler(conn):
    ''' A connection handler that reads one message, sends back the same
    message, then exits. '''
    try:
        msg = await conn.get_message()
        await conn.send_message(msg)
    except ConnectionClosed:
        pass


async def test_listen_port_ipv4():
    assert str(ListenPort('10.105.0.2', 80, False)) == 'ws://10.105.0.2:80'
    assert str(ListenPort('127.0.0.1', 8000, False)) == 'ws://127.0.0.1:8000'
    assert str(ListenPort('0.0.0.0', 443, True)) == 'wss://0.0.0.0:443'


async def test_listen_port_ipv6():
    assert str(ListenPort('2599:8807:6201:b7:16cf:bb9c:a6d3:51ab', 80, False)) \
        == 'ws://[2599:8807:6201:b7:16cf:bb9c:a6d3:51ab]:80'
    assert str(ListenPort('::1', 8000, False)) == 'ws://[::1]:8000'
    assert str(ListenPort('::', 443, True)) == 'wss://[::]:443'


async def test_server_has_listeners(nursery):
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0, None)
    assert len(server.listeners) > 0
    assert isinstance(server.listeners[0], ListenPort)


async def test_serve(nursery):
    task = trio.hazmat.current_task()
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0, None)
    port = server.port
    assert server.port != 0
    # The server nursery begins with one task (server.listen).
    assert len(nursery.child_tasks) == 1
    no_clients_nursery_count = len(task.child_nurseries)
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as conn:
        # The server nursery has the same number of tasks, but there is now
        # one additional nested nursery.
        assert len(nursery.child_tasks) == 1
        assert len(task.child_nurseries) == no_clients_nursery_count + 1


async def test_serve_ssl(nursery):
    server_context = trio.ssl.create_default_context(
        trio.ssl.Purpose.CLIENT_AUTH)
    client_context = trio.ssl.create_default_context()
    ca = trustme.CA()
    ca.configure_trust(client_context)
    cert = ca.issue_server_cert(HOST)
    cert.configure_cert(server_context)
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0,
        server_context)
    port = server.port
    async with open_websocket(HOST, port, RESOURCE, client_context) as conn:
        assert not conn.closed


async def test_serve_handler_nursery(nursery):
    task = trio.hazmat.current_task()
    async with trio.open_nursery() as handler_nursery:
        serve_with_nursery = functools.partial(serve_websocket, echo_handler,
            HOST, 0, None, handler_nursery=handler_nursery)
        server = await nursery.start(serve_with_nursery)
        port = server.port
        # The server nursery begins with one task (server.listen).
        assert len(nursery.child_tasks) == 1
        no_clients_nursery_count = len(task.child_nurseries)
        async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as conn:
            # The handler nursery should have one task in it
            # (conn._reader_task).
            assert len(handler_nursery.child_tasks) == 1


async def test_serve_with_zero_listeners(nursery):
    task = trio.hazmat.current_task()
    with pytest.raises(ValueError):
        server = WebSocketServer(echo_handler, [])


async def test_client_open(echo_server):
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        assert conn.closed is None


async def test_client_open_url(echo_server):
    url = 'ws://{}:{}{}?foo=bar'.format(HOST, echo_server.port, RESOURCE)
    async with open_websocket_url(url) as conn:
        assert conn.path == RESOURCE + '?foo=bar'


async def test_client_open_invalid_url(echo_server):
    with pytest.raises(ValueError):
        async with open_websocket_url('http://foo.com/bar') as conn:
            pass


async def test_client_connect(echo_server, nursery):
    conn = await connect_websocket(nursery, HOST, echo_server.port, RESOURCE,
        use_ssl=False)
    assert conn.closed is None


async def test_client_connect_url(echo_server, nursery):
    url = 'ws://{}:{}{}'.format(HOST, echo_server.port, RESOURCE)
    conn = await connect_websocket_url(nursery, url)
    assert conn.closed is None


async def test_client_send_and_receive(echo_conn):
    async with echo_conn:
        await echo_conn.send_message('This is a test message.')
        received_msg = await echo_conn.get_message()
        assert received_msg == 'This is a test message.'


async def test_client_default_close(echo_conn):
    async with echo_conn:
        assert echo_conn.closed is None
    assert echo_conn.closed.code == 1000
    assert echo_conn.closed.reason is None


async def test_client_nondefault_close(echo_conn):
    async with echo_conn:
        assert echo_conn.closed is None
        await echo_conn.aclose(code=1001, reason='test reason')
    assert echo_conn.closed.code == 1001
    assert echo_conn.closed.reason == 'test reason'
