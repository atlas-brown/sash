class ParseException(Exception):
    def __init__(self, msg):
        self.msg = msg
        
class TimeoutError(Exception):
    def __init__(self, msg):
        self.msg = msg
        
class SyntaxError(Exception):
    pass

class TerminateProgram(Exception):
    pass
class FinishBranch(Exception):
    pass
class EarlyError(RuntimeError):
    def __init__(self, arg):
        self.arg = arg


class StuckExpansion(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info


class ImpureExpansion(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info


class Unimplemented(RuntimeError):
    def __init__(self, msg, ast):
        self.msg = msg
        self.ast = ast


class InvalidVariable(RuntimeError):
    def __init__(self, var, reason):
        self.var = var
        self.reason = reason

class USetUnsetExpansion(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info
        
class SymbArithError(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info
        
class OutOfScopeError(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info
        
ExpansionError = EarlyError | StuckExpansion | ImpureExpansion | Unimplemented | InvalidVariable | USetUnsetExpansion | OutOfScopeError
