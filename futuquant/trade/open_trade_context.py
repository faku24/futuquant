# -*- coding: utf-8 -*-

import pandas as pd
from time import sleep
import datetime as dt
from futuquant.common.open_context_base import OpenContextBase
from futuquant.trade.trade_query import *
from futuquant.quote.response_handler import AsyncHandler_TrdSubAccPush

class OpenTradeContextBase(OpenContextBase):
    """Class for set context of HK stock trade"""

    def __init__(self, trd_mkt, host="127.0.0.1", port=11111):
        self.__trd_mkt = trd_mkt
        self._ctx_unlock = None
        self.__last_acc_list = []
        self.__is_acc_sub_push = False

        super(OpenTradeContextBase, self).__init__(host, port, True)
        self.set_pre_handler(AsyncHandler_TrdSubAccPush(self))

    def close(self):
        """
        to call close old obj before loop create new, otherwise socket will encounter erro 10053 or more!
        """
        super(OpenTradeContextBase, self).close()

    def on_api_socket_reconnected(self):
        """for API socket reconnected"""
        self.__is_acc_sub_push = False

        # auto unlock trade
        if self._ctx_unlock is not None:
            for i in range(3):
                password, password_md5 = self._ctx_unlock
                ret, data = self.unlock_trade(password, password_md5)
                if ret == RET_OK:
                    break
                sleep(1)


    def get_acc_list(self):
        """
        :return: (ret, data)
        """
        query_processor = self._get_sync_query_processor(
            GetAccountList.pack_req, GetAccountList.unpack_rsp)

        kargs = {'user_id': self.get_login_user_id()}

        ret_code, msg, acc_list = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        # 记录当前市场的帐号列表
        self.__last_acc_list = []

        for record in acc_list:
            trdMkt_list = record["trdMarket_list"]
            if self.__trd_mkt in trdMkt_list:
                self.__last_acc_list.append({
                    "trd_env": record["trd_env"],
                    "acc_id": record["acc_id"]})

        col_list = ["acc_id", "trd_env"]

        acc_table = pd.DataFrame(copy(self.__last_acc_list), columns=col_list)

        return RET_OK, acc_table

    def unlock_trade(self, password, password_md5=None, is_unlock=True):
        '''
        交易解锁，安全考虑，所有的交易api,需成功解锁后才可操作
        :param password: 明文密码字符串 (二选一）
        :param password_md5: 密码的md5字符串（二选一）
        :param is_unlock: 解锁 = True, 锁定 = False
        :return:(ret, data) ret == 0 时, data为None
                            ret != 0 时， data为错误字符串
        '''
        # 解锁要求先拉一次帐户列表, 目前仅真实环境需要解锁
        ret, msg, acc_id = self._check_acc_id(TrdEnv.REAL, 0)
        if ret != RET_OK:
            return ret, msg

        query_processor = self._get_sync_query_processor(
                UnlockTrade.pack_req, UnlockTrade.unpack_rsp)

        md5_val = str(password_md5) if not str(password_md5) else md5_transform(str(password))
        kargs = {
            'is_unlock': is_unlock,
            'password_md5': str(md5_val)
        }

        ret_code, msg, _ = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        # reconnected to auto unlock
        if RET_OK == ret_code:
            self._ctx_unlock = (password, password_md5) if is_unlock else None

        # unlock push socket
        ret_code, msg, push_req_str = UnlockTrade.pack_req(**kargs)
        if ret_code == RET_OK:
            self._send_async_req(push_req_str)

        # 定阅交易帐号推送
        if is_unlock and ret_code == RET_OK:
            self.__check_acc_sub_push()

        return RET_OK, None

    def _async_sub_acc_push(self, acc_id_list):
        """
        异步连接指定要接收送的acc id
        :param acc_id:
        :return:
        """
        kargs = {
            'acc_id_list': acc_id_list,
        }
        ret_code, msg, push_req_str = SubAccPush.pack_req(**kargs)
        if ret_code == RET_OK:
            self._send_async_req(push_req_str)

        return RET_OK, None

    def on_async_sub_acc_push(self, ret_code, msg):
        self.__is_acc_sub_push = ret_code == RET_OK
        if not self.__is_acc_sub_push:
            logger.error("ret={} msg={}".format(ret_code, msg))

    def _check_trd_env(self, trd_env):
        is_enable = TRADE.check_mkt_envtype(self.__trd_mkt, trd_env)
        if not is_enable:
            return RET_ERROR, ERROR_STR_PREFIX + "the type of environment param is wrong "

        return RET_OK, ""

    def __check_acc_sub_push(self):
        if self.__is_acc_sub_push:
            return

        if len(self.__last_acc_list) == 0:
            ret, _ = self.get_acc_list()
            if ret != RET_OK:
                return

        acc_id_list = [x['acc_id'] for x in self.__last_acc_list]

        if len(acc_id_list):
            self._async_sub_acc_push(acc_id_list)

    def _check_acc_id(self, trd_env, acc_id):
        if acc_id == 0:
            if len(self.__last_acc_list) == 0:
                ret, content = self.get_acc_list()
                if ret != RET_OK:
                    return ret, content, acc_id
            acc_id = self._get_default_acc_id(trd_env)

        msg = "" if acc_id != 0 else ERROR_STR_PREFIX + "No one available account!"
        ret = RET_OK if acc_id != 0 else RET_ERROR

        # 异步定阅帐号推送
        if ret == RET_OK:
            self.__check_acc_sub_push()

        return ret, msg, acc_id

    def _check_order_status(self, status_filter_list):
        unique_and_normalize_list(status_filter_list)
        for status in status_filter_list:
            if status not in ORDER_STATUS_MAP:
                return RET_ERROR, ERROR_STR_PREFIX + "the type of status_filter_list param is wrong "
        return RET_OK, "",

    def _get_default_acc_id(self, trd_env):
        for record in self.__last_acc_list:
            if  record['trd_env'] == trd_env:
                return record['acc_id']
        return 0

    def accinfo_query(self, trd_env=TrdEnv.REAL, acc_id=0):
        """
        :param trd_env:
        :param acc_id:
        :return:
        """
        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg, acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        query_processor = self._get_sync_query_processor(
            AccInfoQuery.pack_req, AccInfoQuery.unpack_rsp)

        kargs = {'acc_id': int(acc_id), 'trd_env': trd_env, 'trd_market':self.__trd_mkt}

        ret_code, msg, accinfo_list = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = [
            'Power', 'ZCJZ', 'ZQSZ', 'XJJY', 'KQXJ', 'DJZJ'
        ]
        accinfo_frame_table = pd.DataFrame(accinfo_list, columns=col_list)

        return RET_OK, accinfo_frame_table

    def _check_stock_code(self, code):
        stock_code = ''
        if code != '':
            ret_code, content = split_stock_str(str(code))
            if ret_code == RET_OK:
                _, stock_code = content
            else:
                stock_code = code
        return RET_OK, "", stock_code

    def position_list_query(self, code='', pl_ratio_min='', pl_ratio_max='', trd_env=TrdEnv.REAL, acc_id=0):
        """for querying the position list"""
        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg , acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        ret, msg, stock_code = self._check_stock_code(code)
        if ret != RET_OK:
            return ret, msg

        query_processor = self._get_sync_query_processor(
            PositionListQuery.pack_req, PositionListQuery.unpack_rsp)

        kargs = {
            'code': str(stock_code),
            'pl_ratio_min': str(pl_ratio_min),
            'pl_ratio_max': str(pl_ratio_max),
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
        }
        ret_code, msg, position_list = query_processor(**kargs)

        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = [
            "code", "stock_name", "qty", "can_sell_qty", "cost_price",
            "cost_price_valid", "market_val", "nominal_price", "pl_ratio",
            "pl_ratio_valid", "pl_val", "pl_val_valid", "today_buy_qty",
            "today_buy_val", "today_pl_val", "today_sell_qty", "today_sell_val",
            "position_side"
        ]

        position_list_table = pd.DataFrame(position_list, columns=col_list)
        return RET_OK, position_list_table

    def order_list_query(self, order_id="", status_filter_list=[], code='', start='', end='',
                         trd_env=TrdEnv.REAL, acc_id=0):
        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg , acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        ret, msg, stock_code = self._check_stock_code(code)
        if ret != RET_OK:
            return ret, msg

        ret, msg = self._check_order_status(status_filter_list)
        if ret != RET_OK:
            return ret, msg

        query_processor = self._get_sync_query_processor(
            OrderListQuery.pack_req, OrderListQuery.unpack_rsp)

        # the keys of kargs should be corresponding to the actual function arguments
        kargs = {
            'order_id': str(order_id),
            'status_filter_list': status_filter_list,
            'code': str(stock_code),
            'start': str(start) if start else "",
            'end': str(end) if end else "",
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
        }
        ret_code, msg, order_list = query_processor(**kargs)

        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = [
            "code", "stock_name", "trd_side", "order_type", "order_status",
            "order_id", "qty", "price", "create_time", "updated_time",
            "dealt_qty", "dealt_avg_price", "last_err_msg"
        ]
        order_list_table = pd.DataFrame(order_list, columns=col_list)

        return RET_OK, order_list_table

    def place_order(self, price, qty, code, trd_side=TrdSide.NONE, order_type=OrderType.NORMAL,
                    adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0):
        """
        place order
        use  set_handle(HKTradeOrderHandlerBase) to recv order push !
        """
        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg , acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        ret, msg, stock_code = self._check_stock_code(code)
        if ret != RET_OK:
            return ret, msg

        query_processor = self._get_sync_query_processor(
            PlaceOrder.pack_req, PlaceOrder.unpack_rsp)

        # the keys of kargs should be corresponding to the actual function arguments
        kargs = {
            'trd_side': trd_side,
            'order_type': order_type,
            'price': float(price),
            'qty': float(qty),
            'code': str(stock_code),
            'adjust_limit': float(adjust_limit),
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
            'conn_id': self.get_sync_conn_id()
        }

        ret_code, msg, order_id = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = ['trd_env', 'order_id']
        order_list = [{ 'trd_env': trd_env, 'order_id': order_id}]
        order_table = pd.DataFrame(order_list, columns=col_list)

        return RET_OK, order_table

    def modify_order(self, modify_order_op, order_id, qty, price, adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0):

        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg , acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        if not order_id:
            return RET_ERROR, ERROR_STR_PREFIX + "the type of order_id param is wrong "

        if modify_order_op not in MODIFY_ORDER_OP_MAP:
            return RET_ERROR, ERROR_STR_PREFIX + "the type of modify_order_op param is wrong "

        query_processor = self._get_sync_query_processor(
            ModifyOrder.pack_req, ModifyOrder.unpack_rsp)

        kargs = {
            'modify_order_op': modify_order_op,
            'order_id': str(order_id),
            'price': float(price),
            'qty':float(qty),
            'adjust_limit': adjust_limit,
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
            'conn_id': self.get_sync_conn_id(),
        }

        ret_code, msg, modify_order_list = query_processor(**kargs)

        if ret_code != RET_OK:
            return RET_ERROR,msg

        col_list = ['trd_env', 'order_id']
        modify_order_table = pd.DataFrame(modify_order_list, columns=col_list)

        return RET_OK, modify_order_table

    def change_order(self, order_id, price, qty, adjust_limit=0, trd_env=TrdEnv.REAL, acc_id=0):
        return self.modify_order(ModifyOrderOp.NORMAL, order_id, price, qty, adjust_limit, trd_env, acc_id)

    def deal_list_query(self, code="", trd_env=TrdEnv.REAL, acc_id=0):
        """for querying deal list"""
        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg , acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        ret, msg, stock_code = self._check_stock_code(code)
        if ret != RET_OK:
            return ret, msg

        query_processor = self._get_sync_query_processor(
            DealListQuery.pack_req, DealListQuery.unpack_rsp)

        kargs = {
            'code': code,
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
            }
        ret_code, msg, deal_list = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = [
            "code", "stock_name", "deal_id", "order_id", "qty", "price",
            "trd_side", "create_time", "counter_broker_id", "counter_broker_name"
        ]
        deal_list_table = pd.DataFrame(deal_list, columns=col_list)

        return RET_OK, deal_list_table

    def history_order_list_query(self, status_filter_list=[], code='', start='', end='',
                                 trd_env=TrdEnv.REAL, acc_id=0):

        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg , acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        ret, msg, stock_code = self._check_stock_code(code)
        if ret != RET_OK:
            return ret, msg

        ret, msg = self._check_order_status(status_filter_list)
        if ret != RET_OK:
            return ret, msg

        start, end = self._make_start_end_param(start, end, days=90)

        query_processor = self._get_sync_query_processor(
            HistoryOrderListQuery.pack_req,
            HistoryOrderListQuery.unpack_rsp)

        kargs = {
            'status_filter_list': status_filter_list,
            'code': str(stock_code),
            'start': str(start) if start else "",
            'end': str(end) if end else "",
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
        }
        ret_code, msg, order_list = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = [
            "code", "stock_name", "trd_side", "order_type", "order_status",
            "order_id", "qty", "price", "create_time", "updated_time",
            "dealt_qty", "dealt_avg_price", "last_err_msg"
        ]
        order_list_table = pd.DataFrame(order_list, columns=col_list)

        return RET_OK, order_list_table

    def history_deal_list_query(self, code, start, end, trd_env=TrdEnv.REAL, acc_id=0):

        ret, msg = self._check_trd_env(trd_env)
        if ret != RET_OK:
            return ret, msg
        ret, msg, acc_id = self._check_acc_id(trd_env, acc_id)
        if ret != RET_OK:
            return ret, msg

        ret, msg, stock_code = self._check_stock_code(code)
        if ret != RET_OK:
            return ret, msg

        start, end = self._make_start_end_param(start, end, days=90)

        query_processor = self._get_sync_query_processor(
            HistoryDealListQuery.pack_req,
            HistoryDealListQuery.unpack_rsp)

        kargs = {
            'code': str(stock_code),
            'start': str(start) if start else "",
            'end': str(end) if end else "",
            'trd_mkt': self.__trd_mkt,
            'trd_env': trd_env,
            'acc_id': acc_id,
        }
        ret_code, msg, deal_list = query_processor(**kargs)
        if ret_code != RET_OK:
            return RET_ERROR, msg

        col_list = [
            "code", "stock_name", "deal_id", "order_id", "qty", "price",
            "trd_side", "create_time", "counter_broker_id", "counter_broker_name"
        ]
        deal_list_table = pd.DataFrame(deal_list, columns=col_list)

        return RET_OK, deal_list_table

    def _make_start_end_param(self, start, end, days):
        if not start and not end:
            today = dt.date.today()
            end = today.strftime("%Y-%m-%d")
            start = (today - dt.timedelta(days=days)).strftime("%Y-%m-%d")
        elif not start:
            dt_base = dt.datetime.strptime(end, "%Y-%m-%d")
            start = (dt_base - dt.timedelta(days=days)).strftime("%Y-%m-%d")
        elif not end:
            dt_base = dt.datetime.strptime(start, "%Y-%m-%d")
            end = (dt_base + dt.timedelta(days=days)).strftime("%Y-%m-%d")
        return start, end


# 港股交易接口
class OpenHKTradeContext(OpenTradeContextBase):
    def __init__(self, host="127.0.0.1", port=11111):
        super(OpenHKTradeContext, self).__init__(TrdMarket.HK, host, port)


# 美股交易接口
class OpenUSTradeContext(OpenTradeContextBase):
    def __init__(self, host="127.0.0.1", port=11111):
        super(OpenUSTradeContext, self).__init__(TrdMarket.US, host, port)





