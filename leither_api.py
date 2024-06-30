import hprose, json, time
from utilities import UserInDB, is_ipv6, is_local_network_ip

USER_ACCOUNT_KEY = "AICHAT_APP_USER_ACCOUNT_KEY"
GPT_3_Tokens = 1000000      # bonus tokens upon installation
GPT_4_Turbo_Tokens = 10000
LEITHER_SERVER_CLIENT = hprose.HttpClient("http://localhost:8081/webapi/")

class LeitherAPI:
    def __init__(self):
        self.client = LEITHER_SERVER_CLIENT
        print(self.client.GetVar("", "ver"))
        self.ppt = self.client.GetVarByContext("", "context_ppt")
        self.api = self.client.Login(self.ppt)
        self.sid = self.api.sid
        self.uid = self.api.uid
        self.mid = self.client.MMCreate(self.sid, "FmKK37e1T0oGaQJXRMcMjyrmoxa", "app", "aichat index db", 2, 0x07276705)
        print("sid  ", self.api.sid)
        print("uid  ", self.api.uid)
        print("mid  ", self.mid)
        self.sid_time = time.time()

    def get_sid(self):
        if time.time() - self.sid_time > 3600:
            self.ppt = self.client.GetVarByContext("", "context_ppt")
            self.api = self.client.Login(self.ppt)
            self.sid = self.api.sid
            self.uid = self.api.uid
            self.sid_time = time.time()
        return self.sid

    def get_ppt(self, host_id):
        # user's Leither mode ip not used for now.
        print(self.client.GetVar("", "ver"))
        return self.client.RunMApp("main", {"nodeid":host_id, "aid":"ZJZoWhGBcQNnX0vCw60t7R7C3q3","ver":"last"})
    
    def register_in_db(self, user: UserInDB):
        print(user)
        if not self.get_user(user.username):
            # create a mimei for the user and publish it onto web
            user.mid = self.client.MMCreate(self.get_sid(), "FmKK37e1T0oGaQJXRMcMjyrmoxa", "user", user.username, 2, 0x07276705)
            self.client.MiMeiPublish(self.sid, "", user.mid)

            mmsid_cur = self.client.MMOpen(self.get_sid(), self.mid, "cur")
            self.client.Hset(mmsid_cur, USER_ACCOUNT_KEY, user.username, json.dumps(user.model_dump()))
            self.client.MMBackup(self.sid, self.mid, "", "delRef=true")
            print("Registered", user)
            return user
        else:
            return None
    
    def register_cur_node(self, node_id, mid, user):
        self.client.MMSetRight(self.get_sid(), mid, node_id, 0xf)
        self.client.MiMeiPublish(self.sid, "", mid)

        # update host ID of this access
        mmsid = self.client.MMOpen(self.sid, self.mid, "cur")
        user.host_id = node_id
        self.client.Hset(mmsid, USER_ACCOUNT_KEY, user.username, json.dumps(user.model_dump()))
        self.client.MMBackup(self.sid, self.mid, "", "delRef=true")

    def get_user(self, username):
        mmsid = self.client.MMOpen(self.get_sid(), self.mid, "last")
        user = self.client.Hget(mmsid, USER_ACCOUNT_KEY, username)
        if not user:
            return None
        return UserInDB(**json.loads(user))

    def get_users(self):
        mmsid = self.client.MMOpen(self.get_sid(), self.mid, "last")
        return [UserInDB(**json.loads(user.value)) for user in self.client.Hgetall(mmsid, USER_ACCOUNT_KEY)]

    def update_user(self, user_in: UserInDB) -> UserInDB:
        mmsid = self.client.MMOpen(self.get_sid(), self.mid, "last")
        user_in_db = UserInDB(**json.loads(self.client.Hget(mmsid, USER_ACCOUNT_KEY, user_in.username)))
        if user_in.hashed_password is not None and user_in.hashed_password == "":
            # if no pasaword, do not update it.
            del user_in.hashed_password
        for attr in vars(user_in):
            setattr(user_in_db, attr, getattr(user_in, attr))
        mmsid_cur = self.client.MMOpen(self.sid, self.mid, "cur")
        self.client.Hset(mmsid_cur, USER_ACCOUNT_KEY, user_in.username, json.dumps(user_in_db.model_dump()))
        self.client.MMBackup(self.sid, self.mid, "", "delRef=true")
        return user_in_db

    def delete_user(self, username: str):
        mmsid_cur = self.client.MMOpen(self.get_sid(), self.mid, "cur")
        self.client.Hdel(mmsid_cur, USER_ACCOUNT_KEY, username)
        self.client.MMBackup(self.sid, self.mid, "", "delRef=true")

    def bookkeeping(self, llm, total_cost: float, total_tokens: int, user_in_db: UserInDB):
        # mmsid = self.client.MMOpen(self.get_sid(), self.mid, "last")
        # user_in_db = UserInDB(**json.loads(self.client.Hget(mmsid, USER_ACCOUNT_KEY, user.username)))

        if user_in_db.token_usage.get(llm):
            user_in_db.token_usage[llm] += float(total_cost)
        else:
            user_in_db.token_usage[llm] = float(total_cost)

        if user_in_db.token_count.get(llm):
            user_in_db.token_count[llm] += total_tokens
        else:
            user_in_db.token_count[llm] = total_tokens

        mmsid_cur = self.client.MMOpen(self.get_sid(), self.mid, "cur")
        self.client.Hset(mmsid_cur, USER_ACCOUNT_KEY, user_in_db.username, json.dumps(user_in_db.model_dump()))
        self.client.MMBackup(self.sid, self.mid, "", "delRef=true")

    # def get_user(username, password, identifier):
    #     # the password is hashed already
    #     user = json.load(client.Hget(mmsid, USER_ACCOUNT_KEY, identifier))
    #     if not user:
    #         user = {username:username, password:password, "tokenCount":{"gpt-3.5":GPT_3_Tokens, "gpt-4-turbo":GPT_4_Turbo_Tokens}, "tokenUsage":{"gpt-3.5":0, "gpt-4-turbo":0}, "subscription": False, identifier:identifier}
    #         client.Hset(mmsid, USER_ACCOUNT_KEY, identifier, user)
    #     return user
