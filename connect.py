"""Read-only moomoo connection checks. No order or unlock interfaces."""
import argparse
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description="moomoo 美股连接检查（只读）")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--quotes", action="store_true", help="读取 SPY、QQQ 行情快照")
    parser.add_argument("--accounts", action="store_true", help="检查美股模拟账户")
    args = parser.parse_args()
    try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=3):
            pass
    except OSError as exc:
        print("本地连接失败（%s）：请确认 OpenD 已登录、端口 %s 可访问。" % (exc.strerror, args.port))
        return 1

    from moomoo import OpenQuoteContext, OpenSecTradeContext, RET_OK, TrdEnv, TrdMarket, SecurityFirm
    quote = None
    trade = None
    failed = False
    try:
        quote = OpenQuoteContext(host="127.0.0.1", port=args.port)
        ret, state = quote.get_global_state()
        if ret != RET_OK:
            print("OpenD 状态查询失败：", state)
            return 1
        print("OpenD 已连接。")
        for key in ("qot_logined", "trd_logined", "server_ver"):
            if key in state:
                print("%s: %s" % (key, state[key]))
        if args.quotes:
            ret, frame = quote.get_market_snapshot(["US.SPY", "US.QQQ"])
            if ret != RET_OK:
                print("行情读取失败：", str(frame).split("https://")[0])
                failed = True
            else:
                columns = [c for c in ("code", "name", "update_time", "last_price") if c in frame.columns]
                print(frame[columns].to_string(index=False))
        if args.accounts:
            trade = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host="127.0.0.1", port=args.port, security_firm=SecurityFirm.FUTUAU)
            ret, frame = trade.get_acc_list()
            if ret != RET_OK:
                print("模拟账户查询失败：", frame)
                return 1
            simulated = frame[frame["trd_env"] == TrdEnv.SIMULATE]
            if simulated.empty:
                print("没有找到美股模拟账户，请在 moomoo 中确认模拟交易是否已启用。")
                return 1
            print("已找到 %d 个美股模拟账户。" % len(simulated))
            columns = [c for c in ("trd_env", "sim_acc_type", "acc_status") if c in simulated.columns]
            print(simulated[columns].to_string(index=False))
        return 1 if failed else 0
    except Exception as exc:
        print("连接检查失败：", exc)
        return 1
    finally:
        if trade is not None:
            trade.close()
        if quote is not None:
            quote.close()


if __name__ == "__main__":
    sys.exit(main())
