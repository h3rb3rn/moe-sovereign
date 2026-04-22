"""
Math node for the LangGraph workflow.
"""

import sympy as sp
import logging
import re
from typing import Dict, Any, List

# Define AgentState locally for tests
from typing import TypedDict, Annotated
import operator

class AgentState(TypedDict):
    input: str
    plan: List[Dict[str, str]]
    expert_results: Annotated[list, operator.add]
    web_research: str
    cached_facts: str
    math_result: str
    final_response: str

logger = logging.getLogger("MOE-SOVEREIGN")


def parse_math_expression(expr_str: str) -> sp.Expr:
    """
    Parses a mathematical expression from a string.

    Args:
        expr_str (str): The mathematical expression as a string

    Returns:
        sp.Expr: The parsed SymPy expression
    """
    # Normalize common functions and constants to their SymPy equivalents
    replacements = {
        'sqrt': 'sp.sqrt',
        'sin': 'sp.sin',
        'cos': 'sp.cos',
        'tan': 'sp.tan',
        'log': 'sp.log',
        'ln': 'sp.ln',
        'exp': 'sp.exp',
        'pi': 'sp.pi',
        'e': 'sp.E'
    }
    
    for old, new in replacements.items():
        expr_str = expr_str.replace(old, new)

    expr = sp.sympify(expr_str)
    return expr


def solve_equation(equation_str: str) -> Dict[str, Any]:
    """
    Solves a mathematical equation.

    Args:
        equation_str (str): The equation as a string (e.g. "x**2 - 4 = 0")

    Returns:
        Dict[str, Any]: Result containing solutions and related information
    """
    try:
        # Split the equation into left-hand and right-hand sides
        if '=' in equation_str:
            left, right = equation_str.split('=')
            eq = sp.Eq(parse_math_expression(left.strip()), parse_math_expression(right.strip()))
        else:
            # If an expression rather than an equation is given, treat it as equal to 0
            eq = sp.Eq(parse_math_expression(equation_str), 0)

        symbols = list(eq.free_symbols)
        solutions = sp.solve(eq, symbols)
        
        return {
            "success": True,
            "equation": equation_str,
            "symbols": [str(s) for s in symbols],
            "solutions": [str(sol) for sol in solutions],
            "solution_count": len(solutions)
        }
    except Exception as e:
        return {
            "success": False,
            "equation": equation_str,
            "error": str(e)
        }


def simplify_expression(expr_str: str) -> Dict[str, Any]:
    """
    Simplifies a mathematical expression.

    Args:
        expr_str (str): The expression as a string

    Returns:
        Dict[str, Any]: Result containing the simplified expression
    """
    try:
        expr = parse_math_expression(expr_str)
        simplified = sp.simplify(expr)
        
        return {
            "success": True,
            "original": expr_str,
            "simplified": str(simplified),
            "latex": sp.latex(simplified)
        }
    except Exception as e:
        return {
            "success": False,
            "original": expr_str,
            "error": str(e)
        }


def calculate_derivative(expr_str: str, var_str: str = 'x') -> Dict[str, Any]:
    """
    Calculates the derivative of an expression.

    Args:
        expr_str (str): The mathematical expression
        var_str (str): The variable to differentiate with respect to (default: 'x')

    Returns:
        Dict[str, Any]: Result containing the derivative
    """
    try:
        expr = parse_math_expression(expr_str)
        var = sp.Symbol(var_str)
        derivative = sp.diff(expr, var)
        
        return {
            "success": True,
            "expression": expr_str,
            "variable": var_str,
            "derivative": str(derivative),
            "latex": sp.latex(derivative)
        }
    except Exception as e:
        return {
            "success": False,
            "expression": expr_str,
            "variable": var_str,
            "error": str(e)
        }


def calculate_integral(expr_str: str, var_str: str = 'x', definite: bool = False,
                       lower_bound: float = None, upper_bound: float = None) -> Dict[str, Any]:
    """
    Calculates the integral of an expression.

    Args:
        expr_str (str): The mathematical expression
        var_str (str): The variable to integrate with respect to (default: 'x')
        definite (bool): Whether to compute a definite integral (default: False)
        lower_bound (float): Lower bound for definite integration
        upper_bound (float): Upper bound for definite integration

    Returns:
        Dict[str, Any]: Result containing the integral
    """
    try:
        expr = parse_math_expression(expr_str)
        var = sp.Symbol(var_str)
        
        if definite and lower_bound is not None and upper_bound is not None:
            integral = sp.integrate(expr, (var, lower_bound, upper_bound))
            integral_type = "definite"
        else:
            integral = sp.integrate(expr, var)
            integral_type = "indefinite"
        
        return {
            "success": True,
            "expression": expr_str,
            "variable": var_str,
            "integral_type": integral_type,
            "integral": str(integral),
            "latex": sp.latex(integral),
            "lower_bound": lower_bound if definite else None,
            "upper_bound": upper_bound if definite else None
        }
    except Exception as e:
        return {
            "success": False,
            "expression": expr_str,
            "variable": var_str,
            "error": str(e)
        }


async def math_node(state: AgentState) -> Dict[str, Any]:
    """
    LangGraph node for mathematical computations.

    Args:
        state (AgentState): The current graph state

    Returns:
        Dict[str, Any]: Result of the mathematical computation
    """
    logger.debug("--- [NODE] MATH CALCULATION ---")

    user_input = state["input"]

    # Check whether the input is a math request (keyword heuristic)
    math_keywords = ["berechne", "löse", "vereinfache", "integrier", "leite ab", "ableiten", "integral", "=", "funktion"]
    is_math_request = any(keyword in user_input.lower() for keyword in math_keywords)

    # Also match English keywords so both German and English requests are recognized
    english_math_keywords = ["calculate", "solve", "simplify", "integrate", "derive", "derivative", "function"]
    is_math_request = is_math_request or any(keyword in user_input.lower() for keyword in english_math_keywords)
    
    if not is_math_request:
        return {"math_result": "No mathematical request recognized."}
    
    try:
        result_str = ""

        # Search for equations in the text
        equation_pattern = r'([\w\s*+\-/^().]+\s*=\s*[\w\s*+\-/^().]+)'
        equations = re.findall(equation_pattern, user_input)

        # Special handling for German "löse die Gleichung ..." (solve the equation) format
        solve_equation_pattern = r'löse\s+die\s+gleichung\s+(.*)'
        solve_equation_match = re.search(solve_equation_pattern, user_input, re.IGNORECASE)
        
        if solve_equation_match:
            equation = solve_equation_match.group(1).strip()
            result = solve_equation(equation)
            if result["success"]:
                result_str += f"Equation '{result['equation']}' solved:\n"
                result_str += f"Solutions: {', '.join(result['solutions'])}\n\n"
            else:
                result_str += f"Error solving equation '{result['equation']}': {result['error']}\n\n"
        elif equations:
            for equation in equations:
                result = solve_equation(equation.strip())
                if result["success"]:
                    result_str += f"Equation '{result['equation']}' solved:\n"
                    result_str += f"Solutions: {', '.join(result['solutions'])}\n\n"
                else:
                    result_str += f"Error solving equation '{result['equation']}': {result['error']}\n\n"
        
        # Search for expressions to simplify
        simplify_pattern = r'(?:vereinfache|simplify)\s+(.*)'
        simplify_match = re.search(simplify_pattern, user_input, re.IGNORECASE)
        
        if simplify_match:
            expr = simplify_match.group(1).strip()
            result = simplify_expression(expr)
            if result["success"]:
                result_str += f"Expression '{result['original']}' simplified to: {result['simplified']}\n"
                result_str += f"LaTeX: {result['latex']}\n\n"
            else:
                result_str += f"Error simplifying expression '{result['original']}': {result['error']}\n\n"
        
        # Search for derivatives
        derivative_pattern = r'(?:leite\s+(?:die\s+funktion\s+)?|ableiten|derive\s+(?:the\s+function\s+)?)?(.*)(?:\s+ab)?'
        derivative_match = re.search(derivative_pattern, user_input, re.IGNORECASE)

        # Special handling for "Leite die Funktion ... ab" format
        special_derivative_pattern = r'leite\s+die\s+funktion\s+(.*)\s+ab'
        special_derivative_match = re.search(special_derivative_pattern, user_input, re.IGNORECASE)
        
        if special_derivative_match:
            expr_text = special_derivative_match.group(1).strip()
            result = calculate_derivative(expr_text)
            if result["success"]:
                result_str += f"Derivative of '{result['expression']}' with respect to {result['variable']}: {result['derivative']}\n"
                result_str += f"LaTeX: {result['latex']}\n\n"
            else:
                result_str += f"Error computing derivative of '{result['expression']}': {result['error']}\n\n"
        elif derivative_match:
            # General case
            expr_text = derivative_match.group(1).strip()
            # Strip leading German/English command words
            expr_text = re.sub(r'^(leite|ableiten|derive|the\s+function|die\s+Funktion)\s*', '', expr_text, flags=re.IGNORECASE)
            # Strip trailing "ab" if present
            expr_text = re.sub(r'\s+ab$', '', expr_text, flags=re.IGNORECASE)

            # Only proceed if something remains after cleanup
            if expr_text:
                result = calculate_derivative(expr_text)
                if result["success"]:
                    result_str += f"Derivative of '{result['expression']}' with respect to {result['variable']}: {result['derivative']}\n"
                    result_str += f"LaTeX: {result['latex']}\n\n"
                else:
                    result_str += f"Error computing derivative of '{result['expression']}': {result['error']}\n\n"
        
        # Search for integrals
        integral_pattern = r'(?:integrier.*?|integrate)\s+(.*)'
        integral_match = re.search(integral_pattern, user_input, re.IGNORECASE)

        if integral_match:
            expr_text = integral_match.group(1).strip()
            # Strip "die Funktion" prefix if present
            expr_text = re.sub(r'die\s+funktion\s+', '', expr_text, flags=re.IGNORECASE)
            result = calculate_integral(expr_text)
            if result["success"]:
                result_str += f"Integral of '{result['expression']}': {result['integral']}\n"
                result_str += f"LaTeX: {result['latex']}\n\n"
            else:
                result_str += f"Error computing integral of '{result['expression']}': {result['error']}\n\n"

        if result_str:
            return {"math_result": result_str.strip()}
        else:
            return {"math_result": "No mathematical operation recognized or executed."}
    
    except Exception as e:
        logger.error(f"Error in math node: {str(e)}")
        return {"math_result": f"Error during mathematical computation: {str(e)}"}
