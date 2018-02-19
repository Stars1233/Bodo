import numba
from numba import types, typing
from numba.typing.templates import (signature, AbstractTemplate, infer,
                                    ConcreteTemplate, AttributeTemplate, bound_function, infer_global)
from numba.extending import typeof_impl, lower_cast
from numba.extending import type_callable, box, unbox, NativeValue
from numba.extending import models, register_model, infer_getattr
from numba.extending import lower_builtin, overload_method
from numba import cgutils
from llvmlite import ir as lir
import llvmlite.binding as ll


class DictType(types.Opaque):
    def __init__(self, key_typ, val_typ):
        self.key_typ = key_typ
        self.val_typ = val_typ
        super(DictType, self).__init__(
            name='DictType{}{}'.format(key_typ, val_typ))


dict_int_int_type = DictType(types.intp, types.intp)
dict_int32_int32_type = DictType(types.int32, types.int32)


class DictIntInt(object):
    def __new__(cls, *args):
        return {}


class DictInt32Int32(object):
    def __new__(cls, *args):
        return {}


@typeof_impl.register(DictIntInt)
def typeof_index(val, c):
    return dict_int_int_type


@typeof_impl.register(DictInt32Int32)
def typeof_index(val, c):
    return dict_int32_int32_type


@type_callable(DictIntInt)
def type_dict(context):
    def typer():
        return dict_int_int_type
    return typer


@type_callable(DictInt32Int32)
def type_dict(context):
    def typer():
        return dict_int32_int32_type
    return typer


@infer
class SetItemDict(AbstractTemplate):
    key = "setitem"

    def generic(self, args, kws):
        dict_t, idx, value = args
        if isinstance(dict_t, DictType):
            if isinstance(idx, types.Integer):
                return signature(types.none, dict_t, idx, dict_t.val_typ)


@infer
class GetItemDict(AbstractTemplate):
    key = "getitem"

    def generic(self, args, kws):
        dict_t, idx = args
        if isinstance(dict_t, DictType):
            if isinstance(idx, types.Integer):
                return signature(dict_t.val_typ, dict_t, idx)


@infer
class PrintDictIntInt(ConcreteTemplate):
    key = "print_item"
    cases = [signature(types.none, dict_int_int_type),
             signature(types.none, dict_int32_int32_type)]


@infer_getattr
class DictAttribute(AttributeTemplate):
    key = DictType

    @bound_function("dict.get")
    def resolve_get(self, dict, args, kws):
        assert not kws
        assert len(args) == 2
        return signature(args[1], *args)

    @bound_function("dict.pop")
    def resolve_pop(self, dict, args, kws):
        assert not kws
        return signature(dict.val_typ, *args)

    @bound_function("dict.keys")
    def resolve_keys(self, dict, args, kws):
        assert not kws
        return signature(DictKeyIteratorType(dict.key_typ, dict.val_typ))


register_model(DictType)(models.OpaqueModel)


@box(DictType)
def box_dict(typ, val, c):
    """
    """
    # interval = cgutils.create_struct_proxy(typ)(c.context, c.builder, value=val)
    # lo_obj = c.pyapi.float_from_double(interval.lo)
    # hi_obj = c.pyapi.float_from_double(interval.hi)
    class_obj = c.pyapi.unserialize(c.pyapi.serialize_object(DictIntInt))
    res = c.pyapi.call_function_objargs(class_obj, (val,))
    # c.pyapi.decref(lo_obj)
    # c.pyapi.decref(hi_obj)
    c.pyapi.decref(class_obj)
    return res


class DictKeyIteratorType(types.SimpleIterableType):
    def __init__(self, key_typ, val_typ):
        self.key_typ = key_typ
        self.val_typ = val_typ
        super(types.SimpleIterableType, self).__init__(
            'DictKeyIteratorType{}{}'.format(key_typ, val_typ))


dict_key_iterator_int_int_type = DictKeyIteratorType(types.intp, types.intp)
dict_key_iterator_int32_int32_type = DictKeyIteratorType(
    types.int32, types.int32)

register_model(DictKeyIteratorType)(models.OpaqueModel)


@infer_global(min)
@infer_global(max)
class MinMaxDict(AbstractTemplate):
    def generic(self, args, kws):
        if len(args) == 1 and isinstance(args[0], DictKeyIteratorType):
            return signature(args[0].key_typ, *args)


import hdict_ext
ll.add_symbol('init_dict_int_int', hdict_ext.init_dict_int_int)
ll.add_symbol('dict_int_int_setitem', hdict_ext.dict_int_int_setitem)
ll.add_symbol('dict_int_int_print', hdict_ext.dict_int_int_print)
ll.add_symbol('dict_int_int_get', hdict_ext.dict_int_int_get)
ll.add_symbol('dict_int_int_getitem', hdict_ext.dict_int_int_getitem)
ll.add_symbol('dict_int_int_pop', hdict_ext.dict_int_int_pop)
ll.add_symbol('dict_int_int_keys', hdict_ext.dict_int_int_keys)
ll.add_symbol('dict_int_int_min', hdict_ext.dict_int_int_min)
ll.add_symbol('dict_int_int_max', hdict_ext.dict_int_int_max)
ll.add_symbol('dict_int_int_not_empty', hdict_ext.dict_int_int_not_empty)

# int32 versions
ll.add_symbol('init_dict_int32_int32', hdict_ext.init_dict_int32_int32)
ll.add_symbol('dict_int32_int32_setitem', hdict_ext.dict_int32_int32_setitem)
ll.add_symbol('dict_int32_int32_print', hdict_ext.dict_int32_int32_print)
ll.add_symbol('dict_int32_int32_get', hdict_ext.dict_int32_int32_get)
ll.add_symbol('dict_int32_int32_getitem', hdict_ext.dict_int32_int32_getitem)
ll.add_symbol('dict_int32_int32_pop', hdict_ext.dict_int32_int32_pop)
ll.add_symbol('dict_int32_int32_keys', hdict_ext.dict_int32_int32_keys)
ll.add_symbol('dict_int32_int32_min', hdict_ext.dict_int32_int32_min)
ll.add_symbol('dict_int32_int32_max', hdict_ext.dict_int32_int32_max)
ll.add_symbol('dict_int32_int32_not_empty',
              hdict_ext.dict_int32_int32_not_empty)


@lower_builtin(DictIntInt)
def impl_dict_int_int(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(8).as_pointer(), [])
    fn = builder.module.get_or_insert_function(fnty, name="init_dict_int_int")
    return builder.call(fn, [])


@lower_builtin('setitem', DictType, types.intp, types.intp)
def setitem_dict(context, builder, sig, args):
    fnty = lir.FunctionType(lir.VoidType(), [lir.IntType(
        8).as_pointer(), lir.IntType(64), lir.IntType(64)])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int_int_setitem")
    return builder.call(fn, args)


@lower_builtin("print_item", dict_int_int_type)
def print_dict(context, builder, sig, args):
    # pyapi = context.get_python_api(builder)
    # strobj = pyapi.unserialize(pyapi.serialize_object("hello!"))
    # pyapi.print_object(strobj)
    # pyapi.decref(strobj)
    # return context.get_dummy_value()
    fnty = lir.FunctionType(lir.VoidType(), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(fnty, name="dict_int_int_print")
    return builder.call(fn, args)


@lower_builtin("dict.get", DictType, types.intp, types.intp)
def lower_dict_get(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(64), [lir.IntType(
        8).as_pointer(), lir.IntType(64), lir.IntType(64)])
    fn = builder.module.get_or_insert_function(fnty, name="dict_int_int_get")
    return builder.call(fn, args)


@lower_builtin("getitem", DictType, types.intp)
def lower_dict_getitem(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(
        64), [lir.IntType(8).as_pointer(), lir.IntType(64)])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int_int_getitem")
    return builder.call(fn, args)


@lower_builtin("dict.pop", DictType, types.intp)
def lower_dict_pop(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(
        64), [lir.IntType(8).as_pointer(), lir.IntType(64)])
    fn = builder.module.get_or_insert_function(fnty, name="dict_int_int_pop")
    return builder.call(fn, args)


@lower_builtin("dict.keys", dict_int_int_type)
def lower_dict_keys(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(8).as_pointer(), [
                            lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(fnty, name="dict_int_int_keys")
    return builder.call(fn, args)


@lower_builtin(min, dict_key_iterator_int_int_type)
def lower_dict_min(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(64), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(fnty, name="dict_int_int_min")
    return builder.call(fn, args)


@lower_builtin(max, dict_key_iterator_int_int_type)
def lower_dict_max(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(64), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(fnty, name="dict_int_int_max")
    return builder.call(fn, args)


@lower_cast(dict_int_int_type, types.boolean)
def dict_empty(context, builder, fromty, toty, val):
    fnty = lir.FunctionType(lir.IntType(1), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int_int_not_empty")
    return builder.call(fn, (val,))


# ------ int32 versions ------
@lower_builtin(DictInt32Int32)
def impl_dict_int_int(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(8).as_pointer(), [])
    fn = builder.module.get_or_insert_function(
        fnty, name="init_dict_int32_int32")
    return builder.call(fn, [])


@lower_builtin('setitem', DictType, types.int32, types.int32)
def setitem_dict(context, builder, sig, args):
    fnty = lir.FunctionType(lir.VoidType(), [lir.IntType(
        8).as_pointer(), lir.IntType(32), lir.IntType(32)])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_setitem")
    return builder.call(fn, args)


@lower_builtin("print_item", dict_int32_int32_type)
def print_dict(context, builder, sig, args):
    # pyapi = context.get_python_api(builder)
    # strobj = pyapi.unserialize(pyapi.serialize_object("hello!"))
    # pyapi.print_object(strobj)
    # pyapi.decref(strobj)
    # return context.get_dummy_value()
    fnty = lir.FunctionType(lir.VoidType(), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_print")
    return builder.call(fn, args)


@lower_builtin("dict.get", DictType, types.int32, types.int32)
def lower_dict_get(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(32), [lir.IntType(
        8).as_pointer(), lir.IntType(32), lir.IntType(32)])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_get")
    return builder.call(fn, args)


@lower_builtin("getitem", DictType, types.int32)
def lower_dict_getitem(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(
        32), [lir.IntType(8).as_pointer(), lir.IntType(32)])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_getitem")
    return builder.call(fn, args)


@lower_builtin("dict.pop", DictType, types.int32)
def lower_dict_pop(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(
        32), [lir.IntType(8).as_pointer(), lir.IntType(32)])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_pop")
    return builder.call(fn, args)


@lower_builtin("dict.keys", dict_int32_int32_type)
def lower_dict_keys(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(8).as_pointer(), [
                            lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_keys")
    return builder.call(fn, args)


@lower_builtin(min, dict_key_iterator_int32_int32_type)
def lower_dict_min(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(32), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_min")
    return builder.call(fn, args)


@lower_builtin(max, dict_key_iterator_int32_int32_type)
def lower_dict_max(context, builder, sig, args):
    fnty = lir.FunctionType(lir.IntType(32), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_max")
    return builder.call(fn, args)


@lower_cast(dict_int32_int32_type, types.boolean)
def dict_empty(context, builder, fromty, toty, val):
    fnty = lir.FunctionType(lir.IntType(1), [lir.IntType(8).as_pointer()])
    fn = builder.module.get_or_insert_function(
        fnty, name="dict_int32_int32_not_empty")
    return builder.call(fn, (val,))
