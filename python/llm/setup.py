#!/usr/bin/env python

#
# Copyright 2016 The BigDL Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# >> Usage:
#
# >>>> Build for the current platform:
# python setup.py clean --all bdist_wheel
# >>>> Windows:
# python setup.py clean --all bdist_wheel --win
# >>>> Linux：
# python setup.py clean --all bdist_wheel --linux

import fnmatch
import os
import platform
import shutil
import sys
import urllib.request
import requests
import re
import glob
import copy

from setuptools import setup

long_description = '''
    IPEX-LLM is a PyTorch library for running LLM on Intel CPU and GPU (e.g., local PC with iGPU, discrete GPU such as Arc, Flex and Max) with very low latency
'''

exclude_patterns = ["*__pycache__*", "*ipynb_checkpoints*"]
IPEX_LLM_PYTHON_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = open(os.path.join(IPEX_LLM_PYTHON_HOME,
               './llm/version.txt'), 'r').read().strip()
CORE_XE_VERSION = VERSION.replace("2.3.0", "2.7.0")
llm_home = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
github_artifact_dir = os.path.join(llm_home, '../llm-binary')
libs_dir = os.path.join(llm_home, "ipex_llm", "libs")

COMMON_DEP = ["setuptools"]
cpu_torch_version = ["torch==2.1.2+cpu;platform_system=='Linux'", "torch==2.1.2;platform_system=='Windows'"]
CONVERT_DEP = ['numpy == 1.26.4', # lastet 2.0.0b1 will cause error
               'transformers == 4.37.0', 'sentencepiece', 'tokenizers == 0.15.2',
               'accelerate == 0.23.0', 'tabulate'] + cpu_torch_version

SERVING_DEP = ['fschat[model_worker, webui] == 0.2.36', 'protobuf']
windows_binarys = [
    "llama.dll",
    "gptneox.dll",
    "bloom.dll",
    "starcoder.dll",
    "llama-api.dll",
    "gptneox-api.dll",
    "bloom-api.dll",
    "starcoder-api.dll",
    "quantize-llama.exe",
    "quantize-gptneox.exe",
    "quantize-bloom.exe",
    "quantize-starcoder.exe",
    "main-llama.exe",
    "main-gptneox.exe",
    "main-bloom.exe",
    "main-starcoder.exe",
    "libllama_vnni.dll",
    "libgptneox_vnni.dll",
    "libbloom_vnni.dll",
    "libstarcoder_vnni.dll",
    "libllama_avx.dll",
    "libgptneox_avx.dll",
    "libbloom_avx.dll",
    "libstarcoder_avx.dll",
    "quantize-llama_vnni.exe",
    "quantize-gptneox_vnni.exe",
    "quantize-bloom_vnni.exe",
    "quantize-starcoder_vnni.exe",
    "pipeline.dll"
]
linux_binarys = [
    "libllama_avx.so",
    "libgptneox_avx.so",
    "libbloom_avx.so",
    "libstarcoder_avx.so",
    "libllama_avx2.so",
    "libgptneox_avx2.so",
    "libbloom_avx2.so",
    "libstarcoder_avx2.so",
    "libllama_avxvnni.so",
    "libgptneox_avxvnni.so",
    "libbloom_avxvnni.so",
    "libstarcoder_avxvnni.so",
    "libllama_avx512.so",
    "libgptneox_avx512.so",
    "libbloom_avx512.so",
    "libstarcoder_avx512.so",
    "libllama_amx.so",
    "libgptneox_amx.so",
    "libbloom_amx.so",
    "libstarcoder_amx.so",
    "quantize-llama",
    "quantize-gptneox",
    "quantize-bloom",
    "quantize-starcoder",
    "libllama-api.so",
    "libgptneox-api.so",
    "libbloom-api.so",
    "libstarcoder-api.so",
    "main-llama",
    "main-gptneox",
    "main-bloom",
    "main-starcoder"
]

ext_lib_urls = [
    "https://github.com/analytics-zoo/jemalloc/releases/download/v5.3.0/libjemalloc.so",
    "https://github.com/analytics-zoo/tcmalloc/releases/download/v2.10/libtcmalloc.so"
]

ext_libs = [
    "libjemalloc.so",
    "libtcmalloc.so"
]



def get_llm_packages():
    llm_packages = []
    for dirpath, _, _ in os.walk(os.path.join(llm_home, "ipex_llm")):
        print(dirpath)
        package = dirpath.split(llm_home + os.sep)[1].replace(os.sep, '.')
        if any(fnmatch.fnmatchcase(package, pat=pattern)
               for pattern in exclude_patterns):
            print("excluding", package)
        else:
            llm_packages.append(package)
            print("including", package)
    return llm_packages


def obtain_lib_urls():
    base_url = "https://sourceforge.net/projects/analytics-zoo/files/bigdl-llm/"

    def get_date_urls(base_url):
        # obtain all urls based on date(format: xxxx-xx-xx)
        text = ''
        try:
            text = requests.get(base_url).text
        except Exception as e:
            print("error - > ", base_url, e)
            pass
        reg = "https://sourceforge.net/projects/analytics-zoo/files/bigdl-llm/[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}/"
        urls = re.findall(reg, text)
        return urls

    def get_urls_for_binary(date_urls, binarys):
        # Sort by time from near to far
        date_urls = sorted(date_urls, reverse=True)
        binary_url = {}
        download_num = len(binarys)
        for url in date_urls:
            try:
                text = requests.get(url).text
            except Exception as e:
                print("error - > ", url, e)
                continue
            for binary in binarys:
                if binary in binary_url:
                    continue
                # Filename hard matching
                match_pattern = "\"name\":\"{}\"".format(binary)
                if match_pattern in text:
                    lib_url = url + binary
                    binary_url[binary] = lib_url
                    download_num -= 1
                    if download_num == 0:
                        break
            if download_num == 0:
                break
        return binary_url

    lib_urls = {}
    date_urls = get_date_urls(base_url)
    windows_binary_urls = get_urls_for_binary(date_urls, windows_binarys)
    lib_urls["Windows"] = list(windows_binary_urls.values())
    linux_binary_urls = get_urls_for_binary(date_urls, linux_binarys)
    lib_urls["Linux"] = list(linux_binary_urls.values()) + ext_lib_urls
    return lib_urls


def download_libs(url: str, change_permission=False):
    libso_file_name = url.split('/')[-1]
    libso_file = os.path.join(libs_dir, libso_file_name)
    if not os.path.exists(libso_file):
        print(">> Downloading from ", url)
        urllib.request.urlretrieve(url, libso_file)
    else:
        print('>> Skip downloading ', libso_file)
    if change_permission:
        os.chmod(libso_file, 0o775)


def setup_package():
    package_data = {}
    package_data["Windows"] = list(map(lambda x: os.path.join('libs', x),
                                       windows_binarys))
    package_data["Linux"] = list(map(lambda x: os.path.join('libs', x),
                                     linux_binarys + ext_libs))
    platform_name = None
    if "--win" in sys.argv:
        platform_name = "Windows"
        sys.argv.remove("--win")
    if "--linux" in sys.argv:
        platform_name = "Linux"
        sys.argv.remove("--linux")

    if platform_name is None:
        if platform.platform().startswith('Windows'):
            platform_name = "Windows"
        else:
            platform_name = "Linux"

    change_permission = True if platform_name == "Linux" else False

    # Delete legacy libs
    if os.path.exists(libs_dir):
        print(f"Deleting existing libs_dir {libs_dir} ....")
        shutil.rmtree(libs_dir)
    os.makedirs(libs_dir, exist_ok=True)
    open(os.path.join(libs_dir, "__init__.py"), 'w').close()

    # copy built files for github workflow
    for built_file in glob.glob(os.path.join(github_artifact_dir, '*')):
        print(f'Copy workflow built file: {built_file}')
        if change_permission:
            os.chmod(built_file, 0o775)
        shutil.copy(built_file, libs_dir)

    lib_urls = obtain_lib_urls()

    for url in lib_urls[platform_name]:
        download_libs(url, change_permission=change_permission)

    # Check if all package files are ready
    for file in package_data[platform_name]:
        file_path = os.path.join(libs_dir, os.path.basename(file))
        if not os.path.exists(file_path):
            print(f'Could not find package dependency file: {file_path}')
            raise FileNotFoundError(
                f'Could not find package dependency file: {file_path}')

    all_requires = ['py-cpuinfo', 'protobuf',
                    "intel-openmp; (platform_machine=='x86_64' or platform_machine == 'AMD64')",
                    'mpmath==1.3.0' # fix AttributeError: module 'mpmath' has no attribute 'rational'
                    ]
    all_requires += CONVERT_DEP
    all_requires += COMMON_DEP

    # Add internal requires for llama-index
    llama_index_requires = copy.deepcopy(all_requires)
    for exclude_require in cpu_torch_version:
        llama_index_requires.remove(exclude_require)
    llama_index_requires.remove("setuptools")
    llama_index_requires += ["setuptools<70.0.0"]
    llama_index_requires += ["torch<2.2.0",
                             "sentence-transformers~=2.6.1"]


    oneapi_2024_0_requires = ["dpcpp-cpp-rt==2024.0.2;platform_system=='Windows'",
                              "mkl-dpcpp==2024.0.0;platform_system=='Windows'"]

    # Linux install with --extra-index-url https://pytorch-extension.intel.com/release-whl/stable/xpu/us/
    xpu_21_requires = copy.deepcopy(all_requires)
    for exclude_require in cpu_torch_version:
        xpu_21_requires.remove(exclude_require)
    xpu_21_requires.remove("setuptools")
    xpu_21_requires += ["setuptools<70.0.0"]
    xpu_21_requires += ["torch==2.1.0a0",
                        "torchvision==0.16.0a0",
                        "intel_extension_for_pytorch==2.1.10+xpu",
                        "bigdl-core-xe-21==" + CORE_XE_VERSION,
                        "bigdl-core-xe-batch-21==" + CORE_XE_VERSION,
                        "bigdl-core-xe-addons-21==" + CORE_XE_VERSION]
    xpu_21_requires += oneapi_2024_0_requires
    # default to ipex 2.1 for linux and windows
    xpu_requires = copy.deepcopy(xpu_21_requires)
    
    xpu_26_requires = copy.deepcopy(all_requires)
    for exclude_require in cpu_torch_version:
        xpu_26_requires.remove(exclude_require)
    xpu_26_requires += ["torch==2.6.0+xpu",
                        "torchvision==0.21.0+xpu",
                        "torchaudio==2.6.0+xpu",
                        "bigdl-core-xe-all==" + CORE_XE_VERSION,
                        "onednn-devel==2025.0.1;platform_system=='Windows'",
                        "onednn==2025.0.1;platform_system=='Windows'",
                        "dpcpp-cpp-rt==2025.0.2"]

    # Add for testing purposes for now, for Arrow Lake-H with AOT on Windows
    # Linux keeps the same as xpu_2.6
    xpu_26_arl_requires = copy.deepcopy(all_requires)
    for exclude_require in cpu_torch_version:
        xpu_26_arl_requires.remove(exclude_require)
    xpu_26_arl_requires += ["torch==2.6.0.post0+xpu;platform_system=='Windows'",
                            "torchvision==0.21.0.post0+xpu;platform_system=='Windows'",
                            "torchaudio==2.6.0.post0+xpu;platform_system=='Windows'",
                            "torch==2.6.0+xpu;platform_system=='Linux'",
                            "torchvision==0.21.0+xpu;platform_system=='Linux'",
                            "torchaudio==2.6.0+xpu;platform_system=='Linux'",
                            "bigdl-core-xe-all==" + CORE_XE_VERSION,
                            "onednn-devel==2025.0.1;platform_system=='Windows'",
                            "onednn==2025.0.1;platform_system=='Windows'",
                            "dpcpp-cpp-rt==2025.0.2"]

    cpp_requires = ["bigdl-core-cpp==" + CORE_XE_VERSION,
                    "onednn-devel==2025.0.1;platform_system=='Windows'",
                    "onednn==2025.0.1;platform_system=='Windows'",
                    "dpcpp-cpp-rt==2025.0.2;platform_system=='Windows'",
                    "mkl-dpcpp==2025.0.1;platform_system=='Windows'"]
    cpp_requires += COMMON_DEP

    serving_requires = ['py-cpuinfo']
    serving_requires += SERVING_DEP
    serving_requires += COMMON_DEP

    npu_requires = copy.deepcopy(all_requires)
    cpu_transformers_version = ['transformers == 4.37.0', 'tokenizers == 0.15.2']
    for exclude_require in cpu_transformers_version:
        npu_requires.remove(exclude_require)
    npu_requires += ["transformers==4.40.0",
                     "bigdl-core-npu==" + CORE_XE_VERSION + ";platform_system=='Windows'"]

    metadata = dict(
        name='ipex_llm',
        version=VERSION,
        description='Large Language Model Develop Toolkit',
        long_description=long_description,
        long_description_content_type="text/markdown",
        author='BigDL Authors',
        author_email='bigdl-user-group@googlegroups.com',
        license='Apache License, Version 2.0',
        url='https://github.com/intel-analytics/ipex-llm',
        packages=get_llm_packages(),
        package_dir={"": "src"},
        package_data={
            "ipex_llm": package_data[platform_name] + ["cli/prompts/*.txt"] + ["transformers/gguf/models/model_implement/*/*.json"]},
        include_package_data=True,
        entry_points={
            "console_scripts": [
                'llm-convert=ipex_llm.convert_model:main'
            ]
        },
        extras_require={"all": all_requires,
                        "xpu": xpu_requires,  # default to ipex 2.1 for linux and windows
                        "npu": npu_requires,
                        "xpu-2-1": xpu_21_requires,
                        "xpu-2-6": xpu_26_requires,
                        "xpu-2-6-arl": xpu_26_arl_requires,
                        "serving": serving_requires,
                        "cpp": cpp_requires,
                        "llama-index": llama_index_requires}, # for internal usage when upstreaming for llama-index
        classifiers=[
            'License :: OSI Approved :: Apache Software License',
            'Programming Language :: Python :: 3',
            'Programming Language :: Python :: 3.11',
            'Programming Language :: Python :: Implementation :: CPython'],
        scripts={
            'Linux': ['src/ipex_llm/cli/llm-cli', 'src/ipex_llm/cli/llm-chat', 'scripts/ipex-llm-init'],
            'Windows': ['src/ipex_llm/cli/llm-cli.ps1', 'src/ipex_llm/cli/llm-chat.ps1', 'scripts/ipex-llm-init.bat'],
        }[platform_name],
        platforms=['windows']
    )

    setup(**metadata)


if __name__ == '__main__':
    setup_package()
