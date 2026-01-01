# TODO: trim or drop this file
class Config:
    __conf = {
    "CONCRETE_CWD": True,
    "CWD_PATH" : "/",
    "ERROR_ON_FAILURE": True,
    "FULL_REPR": False,
    "TIME_LIMIT": 5,
    "SMTFILE": False,
    "SMTSTATS": False,
    "LOCALVARFILE" : "local_vars.txt",
    "WHILE_UNROLL_LIMIT" : 2,
    "MAX_UNROLL_LIMIT" : 2,
    "SMTTIMEOUT" : 6_500, #The unit here is milliseconds,
    "ASSUME_SIDE_EFFECTS" : False,
    "DEBUG":True,
    "SCCHECK" : False,
    "CREATE_Z3" : False,
    "PROTECTED_PATHS" : ["/", "/*",
                         "/bin",
                         "/etc","/sbin","/var","/lib","/lib64","/home"] + [usr_path + thing for usr_path in ["/usr", "/usr/local"] for thing in ["", "/bin","/sbin","/lib","/share", "/include"]],
    "SAFE_OVERWRITE_PATHS": ["/dev/null", "/dev/stdout", "/dev/stderr"],
    }
    __setters = ["DEBUG","CREATE_Z3"]

    @staticmethod
    def get(name):
        return Config.__conf[name]

    @staticmethod
    def set(name, value):
        if name in Config.__setters:
            Config.__conf[name] = value
        else:
            raise ValueError(f"Only {Config.__setters} can be modified")
