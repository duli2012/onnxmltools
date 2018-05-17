# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

import six
from distutils.version import StrictVersion
from ...proto import helper, onnx_proto


class RawModelContainer(object):
    '''
    This container is the carrier of the model we want to convert. It provides an abstract layer so that our parsing
    framework can work with models generated by different tools.
    '''

    def __init__(self, raw_model):
        self._raw_model = raw_model

    @property
    def raw_model(self):
        return self._raw_model

    @property
    def input_names(self):
        '''
        This function should return a list of strings. Each string corresponds to an input variable name.
        :return: a list of string
        '''
        raise NotImplementedError()

    @property
    def output_names(self):
        '''
        This function should return a list of strings. Each string corresponds to an output variable name.
        :return: a list of string
        '''
        raise NotImplementedError()


class CoremlModelContainer(RawModelContainer):

    def __init__(self, coreml_model):
        super(CoremlModelContainer, self).__init__(coreml_model)

    @property
    def input_names(self):
        return [str(var.name) for var in self.raw_model.description.input]

    @property
    def output_names(self):
        return [str(var.name) for var in self.raw_model.description.output]


class SklearnModelContainer(RawModelContainer):

    def __init__(self, sklearn_model):
        super(SklearnModelContainer, self).__init__(sklearn_model)
        # Scikit-learn models have no input and output specified, so we create them and store them in this container.
        self._inputs = []
        self._outputs = []

    @property
    def input_names(self):
        return [variable.raw_name for variable in self._inputs]

    @property
    def output_names(self):
        return [variable.raw_name for variable in self._outputs]

    def add_input(self, variable):
        # The order of adding variables matters. The final model's input names are sequentially added as this list
        if variable not in self._inputs:
            self._inputs.append(variable)

    def add_output(self, variable):
        # The order of adding variables matters. The final model's output names are sequentially added as this list
        if variable not in self._outputs:
            self._outputs.append(variable)


class KerasModelContainer(RawModelContainer):

    def __init__(self, keras_model):
        super(KerasModelContainer, self).__init__(keras_model)
        self._input_raw_names = list()
        self._output_raw_names = list()

    def add_input_name(self, name):
        # The order of adding strings matters. The final model's input names are sequentially added as this list
        if name not in self._input_raw_names:
            self._input_raw_names.append(name)

    def add_output_name(self, name):
        # The order of adding strings matters. The final model's output names are sequentially added as this list
        if name not in self._output_raw_names:
            self._output_raw_names.append(name)

    @property
    def input_names(self):
        return [name for name in self._input_raw_names]

    @property
    def output_names(self):
        return [name for name in self._output_raw_names]


class ModelComponentContainer:
    '''
    In the conversion phase, this class is used to collect all materials required to build an ONNX GraphProto, which is
    encapsulated in a ONNX ModelProto.
    '''

    def __init__(self, targeted_onnx, float_precision=32):
        '''
        :param float_precision: precision of float number, 32 bits or 16 bits
        :param targeted_onnx: A string, for example, '1.1.2' and '1.2'.
        '''
        # Inputs of ONNX graph. They are ValueInfoProto in ONNX.
        self.inputs = []
        # Outputs of ONNX graph. They are ValueInfoProto in ONNX.
        self.outputs = []
        # ONNX tensors (type: TensorProto). They are initializers of ONNX GraphProto.
        self.initializers = []
        # Intermediate variables in ONNX computational graph. They are ValueInfoProto in ONNX.
        self.value_info = []
        # ONNX nodes (type: NodeProto) used to define computation structure
        self.nodes = []
        # ONNX operators' domain-version pair set. They will be added into opset_import field in the final ONNX model.
        self.node_domain_version_pair_sets = set()
        # Precision of float numbers: 16 or 32
        if float_precision != 16 and float_precision != 32:
            raise ValueError('float precision can only be 16 or 32')
        else:
            self.float_precision = float_precision
        # The targeted ONNX version. All produced operators should be supported by the targeted ONNX version.
        self.targeted_onnx_version = StrictVersion(targeted_onnx)

    def _make_value_info(self, variable):
        value_info = helper.ValueInfoProto()
        value_info.name = variable.full_name
        value_info.type.CopyFrom(variable.type.to_onnx_type())
        if value_info.type.tensor_type.elem_type == onnx_proto.TensorProto.FLOAT:
            if(self.float_precision == 16) :
                value_info.type.tensor_type.elem_type = onnx_proto.TensorProto.FLOAT16
        if variable.type.doc_string:
            value_info.doc_string = variable.type.doc_string
        return value_info

    def add_input(self, variable):
        '''
        Add our Variable object defined _parser.py into the the input list of the final ONNX model

        :param variable: The Variable object to be added
        '''
        self.inputs.append(self._make_value_info(variable))

    def add_output(self, variable):
        '''
        Add our Variable object defined _parser.py into the the output list of the final ONNX model

        :param variable: The Variable object to be added
        '''
        self.outputs.append(self._make_value_info(variable))

    def add_initializer(self, name, onnx_type, shape, content):
        '''
        Add a TensorProto into the initializer list of the final ONNX model

        :param name: Variable name in the produced ONNX model.
        :param onnx_type: Element types allowed in ONNX tensor, e.g., TensorProto.FLOAT and TensorProto.STRING.
        :param shape: Tensor shape, a list of integers.
        :param content: Flattened tensor values (i.e., a float list or a float array).
        '''
        if (self.float_precision == 16 and onnx_type == onnx_proto.TensorProto.FLOAT):
            onnx_type = onnx_proto.TensorProto.FLOAT16
        if any(d is None for d in shape):
            raise ValueError('Shape of initializer cannot contain None')
        tensor = helper.make_tensor(name, onnx_type, shape, content)
        self.initializers.append(tensor)

    def add_value_info(self, variable):
        self.value_info.append(self._make_value_info(variable))

    def add_node(self, op_type, inputs, outputs, op_domain='', op_version=1, **attrs):
        '''
        Add a NodeProto into the node list of the final ONNX model. If the input operator's domain-version information
        cannot be found in our domain-version pool (a Python set), we may add it.

        :param op_type: A string (e.g., Pool and Conv) indicating the type of the NodeProto
        :param inputs: A list of strings. They are the input variables' names of the considered NodeProto
        :param outputs: A list of strings. They are the output variables' names of the considered NodeProto
        :param op_domain: The domain name (e.g., ai.onnx.ml) of the operator we are trying to add.
        :param op_version: The version number (e.g., 0 and 1) of the operator we are trying to add.
        :param attrs: A Python dictionary. Keys and values are attributes' names and attributes' values, respectively.
        '''

        if isinstance(inputs, (six.string_types, six.text_type)):
            inputs = [inputs]
        if isinstance(outputs, (six.string_types, six.text_type)):
            outputs = [outputs]
        if not isinstance(inputs, list) or not all(isinstance(s, (six.string_types, six.text_type)) for s in inputs):
            type_list = ','.join(list(str(type(s)) for s in inputs))
            raise ValueError('Inputs must be a list of string but get [%s]' % type_list)
        if not isinstance(outputs, list) or not all(isinstance(s, (six.string_types, six.text_type)) for s in outputs):
            type_list = ','.join(list(str(type(s)) for s in outputs))
            raise ValueError('Outputs must be a list of string but get [%s]' % type_list)
        for k, v in attrs.items():
            if v is None:
                raise ValueError('Failed to create ONNX node. Undefined attribute pair (%s, %s) found' % (k, v))

        node = helper.make_node(op_type, inputs, outputs, **attrs)
        node.domain = op_domain

        self.node_domain_version_pair_sets.add((op_domain, op_version))
        self.nodes.append(node)
