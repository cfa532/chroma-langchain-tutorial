import hprose, json
from utilities import UserIn, UserOut, UserInDB

client = hprose.HttpClient('http://localhost:8004/webapi/')
print(client.GetVar("", "ver"))
ppt = client.GetVarByContext("", "context_ppt")
api = client.Login(ppt)
print("reply", api)
print("sid  ", api.sid)
print("uid  ", api.uid)

userAccountKey = "AJCHAT_APP_USER_ACCOUNT_KEY"
GPT_3_Tokens = 1000000      # bonus tokens upon installation
GPT_4_Turbo_Tokens = 10000

mid = client.MMCreate(api.sid, api.uid, "", "ajchat app db", 2, 0x07276705)
print("mid  ", mid)

def register_in_db(user: UserInDB):
    print(user)
    u = get_user(user.username)
    if not u:
        mmsid_cur = client.MMOpen(api.sid, mid, "cur")
        client.Hset(mmsid_cur, userAccountKey, user.username, json.dumps(user.model_dump()))
        client.MMBackup(api.sid, mid, "", "delRef=true")
        return True
    else:
        return False
    
def get_user(username):
    mmsid = client.MMOpen(api.sid, mid, "last")
    user = client.Hget(mmsid, userAccountKey, username)
    if not user:
        return None
    return UserInDB(**json.loads(user))

def get_users():
    mmsid = client.MMOpen(api.sid, mid, "last")
    return [UserInDB(**json.loads(user.value)) for user in client.Hgetall(mmsid, userAccountKey)]

def update_user(user: UserInDB):
    mmsid = client.MMOpen(api.sid, mid, "last")
    user_in_db = UserInDB(**json.loads(client.Hget(mmsid, userAccountKey, user.username)))
    for attr in vars(user):
        setattr(user_in_db, attr, getattr(user, attr))
    mmsid_cur = client.MMOpen(api.sid, mid, "cur")
    client.Hset(mmsid_cur, userAccountKey, user.username, json.dumps(user_in_db.model_dump()))
    client.MMBackup(api.sid, mid, "", "delRef=true")

def delete_user(username: str):
    mmsid_cur = client.MMOpen(api.sid, mid, "cur")
    client.Hdel(mmsid_cur, userAccountKey, username)
    client.MMBackup(api.sid, mid, "", "delRef=true")

# def get_user(username, password, identifier):
#     # the password is hashed already
#     user = json.load(client.Hget(mmsid, userAccountKey, identifier))
#     if not user:
#         user = {username:username, password:password, "tokenCount":{"gpt-3.5":GPT_3_Tokens, "gpt-4-turbo":GPT_4_Turbo_Tokens}, "tokenUsage":{"gpt-3.5":0, "gpt-4-turbo":0}, "subscription": False, identifier:identifier}
#         client.Hset(mmsid, userAccountKey, identifier, user)
#     return user
