from __future__ import annotations

try:  # pragma: no cover - package import path
    import tron_roll_call_hero.runtime_context as ctx
except ImportError:  # pragma: no cover - direct script fallback
    import runtime_context as ctx  # type: ignore


def __getattr__(name: str):
    return getattr(ctx, name)



async def bot_serve_command(args: ctx.argparse.Namespace) -> int:
    try:
        from tron_roll_call_hero.adapter_server import run_adapter_server
        from tron_roll_call_hero.bot_handlers import create_bot_runtime
        from tron_roll_call_hero.discord_adapter import create_discord_notification_sink
        from tron_roll_call_hero.line_adapter import create_line_notification_sink
        from tron_roll_call_hero.telegram_adapter import create_telegram_notification_sink
    except ImportError:
        from adapter_server import run_adapter_server
        from bot_handlers import create_bot_runtime
        from discord_adapter import create_discord_notification_sink
        from line_adapter import create_line_notification_sink
        from telegram_adapter import create_telegram_notification_sink
    host = args.host or '127.0.0.1'
    port = int(args.port)
    adapter = args.adapter or 'all'
    supervisor_mode = bool(getattr(args, 'supervisor', False))
    monitor_app = None
    dashboard = None
    if supervisor_mode:
        try:
            from tron_roll_call_hero.bot_supervisor_bridge import create_supervised_bot_runtime
            from tron_roll_call_hero.dashboard_events import DashboardEventStore
            from tron_roll_call_hero.dashboard_server import attach_dashboard
        except ImportError:
            from bot_supervisor_bridge import create_supervised_bot_runtime
            from dashboard_events import DashboardEventStore
            from dashboard_server import attach_dashboard
        event_store = DashboardEventStore(ctx.BASE_DIR)
        monitor_app, runtime = create_supervised_bot_runtime(
            ctx.CONFIG, base_dir=ctx.BASE_DIR, event_sink=event_store
        )
        startup = await monitor_app.start()
        if not startup.ok:
            print('Supervisor 啟動失敗：沒有可監控的帳號（{}）。'.format(startup.kind or 'unknown'))
            await monitor_app.stop()
            return 1
        print('Supervisor 已啟動帳號：{}{}'.format(
            ', '.join(startup.started),
            '；略過：{}'.format(', '.join(item.get('user', '') for item in startup.skipped)) if startup.skipped else '',
        ))
        dashboard = attach_dashboard(monitor_app, event_store, host=host, port=port)
        print('Dashboard: {}'.format(dashboard[2]))
    else:
        runtime = create_bot_runtime(ctx.CONFIG, base_dir=ctx.BASE_DIR)
    line_sink = create_line_notification_sink(ctx.CONFIG) if adapter in {'all', 'line'} else None
    discord_sink = create_discord_notification_sink(ctx.CONFIG) if adapter in {'all', 'discord'} else None
    telegram_sink = create_telegram_notification_sink(ctx.CONFIG) if adapter == 'all' else None
    new_sinks = [sink for sink in (line_sink, discord_sink, telegram_sink) if sink is not None]
    original_sinks = list(ctx.NOTIFICATION_SINKS)
    if new_sinks:
        ctx.set_notification_sinks(original_sinks + new_sinks)
    if getattr(args, 'json', False):
        print(ctx.json_text({'host': host, 'port': port, 'adapter': adapter, 'supervisor': supervisor_mode}))
    else:
        print('Bot adapter server listening on http://{}:{} ({})'.format(host, port, adapter))
    try:
        await run_adapter_server(
            ctx.CONFIG,
            runtime,
            host=host,
            port=port,
            adapter=adapter,
            configure_app=dashboard[0] if dashboard is not None else None,
        )
    finally:
        if new_sinks:
            ctx.set_notification_sinks(original_sinks)
        if monitor_app is not None:
            await monitor_app.stop()
    return 0


def bot_discord_schema_command(args: ctx.argparse.Namespace) -> int:
    try:
        from tron_roll_call_hero.discord_adapter import build_discord_command_schema
    except ImportError:
        from discord_adapter import build_discord_command_schema
    schema = build_discord_command_schema()
    print(ctx.json_text(schema))
    return 0


async def bot_discord_sync_command(args: ctx.argparse.Namespace) -> int:
    report = await ctx.sync_discord_command_schema(ctx.CONFIG, dry_run=not bool(getattr(args, 'apply', False)), apply=bool(getattr(args, 'apply', False)))
    if getattr(args, 'json', False):
        print(ctx.json_text(report))
    else:
        print('Discord command sync: {}'.format(report.get('status', 'unknown')))
        print('Dry run: {}'.format('yes' if report.get('dry_run') else 'no'))
    return 0 if report.get('status') in {'dry_run', 'ok'} else 1


async def bot_discord_gateway_command(args: ctx.argparse.Namespace) -> int:
    if getattr(args, 'dry_run', False):
        report = ctx.build_gateway_health(ctx.CONFIG)
        if getattr(args, 'json', False):
            print(ctx.json_text(report))
        else:
            print('Discord Gateway optional: {}'.format(report.get('status', 'unknown')))
            print('HTTP Interactions recommended: yes')
        return 0
    monitor_app = None
    if getattr(args, 'supervisor', False):
        try:
            from tron_roll_call_hero.bot_supervisor_bridge import create_supervised_bot_runtime
        except ImportError:
            from bot_supervisor_bridge import create_supervised_bot_runtime
        monitor_app, runtime = create_supervised_bot_runtime(ctx.CONFIG, base_dir=ctx.BASE_DIR)
        startup = await monitor_app.start()
        if not startup.ok:
            print('Supervisor 啟動失敗：沒有可監控的帳號（{}）。'.format(startup.kind or 'unknown'))
            await monitor_app.stop()
            return 1
        print('Supervisor 已啟動帳號：{}'.format(', '.join(startup.started)))
    else:
        try:
            from tron_roll_call_hero.bot_handlers import create_bot_runtime
        except ImportError:
            from bot_handlers import create_bot_runtime
        runtime = create_bot_runtime(ctx.CONFIG, base_dir=ctx.BASE_DIR)
    if getattr(args, 'json', False):
        print(ctx.json_text({'status': 'starting', 'gateway_optional': True, 'supervisor': monitor_app is not None}))
    else:
        print('Starting optional Discord Gateway. HTTP Interactions remains the recommended production entry.')
    try:
        await ctx.run_discord_gateway(ctx.CONFIG, runtime)
    finally:
        if monitor_app is not None:
            await monitor_app.stop()
    return 0
